import torch, wandb, math

from torch import nn
from torch.utils.data import DataLoader
from tqdm import tqdm
from typing import Tuple, Union, Optional


from mypt.models.simClr.simClrModel import SimClrModel
from mypt.code_utilities import pytorch_utilities as pu
from mypt.losses.simClrLoss import SimClrLoss
from mypt.similarities.cosineSim import CosineSim

def train_per_batch(model: SimClrModel,
                    x1_batch: torch.Tensor,
                    x2_batch: torch.Tensor,
                    loss_function: SimClrLoss,
                    optimizer: torch.optim.Optimizer,
                    optimizer_zero_grad:bool,
                    optimizer_step:bool,
                    device: str,
                    batch_stats: bool=False
                    ) -> Union[float, Tuple[float, float]]:

    model.to(device=device)
    # make sure to stack the two batches into a single batch 
    x = torch.cat([x1_batch, x2_batch]).to(device=device)
    
    if optimizer_zero_grad:
        # set the optimizer's gradients to zero
        optimizer.zero_grad()
    
    # forward pass through the model
    _, g_x = model.forward(x)

    # get a deep copy for the batch representation
    if batch_stats:
        g_x_copy = g_x.detach()
        # compute the similarities between the
        similarities = CosineSim().forward(g_x_copy, g_x_copy)

        avg_mean_sample_sims = torch.mean(similarities.mean(dim=1)).item()
        avg_min_sample_sims = torch.mean(similarities.min(dim=1)[0]).item()
        avg_max_sample_sims = torch.mean((similarities * (similarities != 1)).max(dim=1)[0]).item()
        avg_std_sample_sims = torch.mean(similarities.std(dim=1)).item()

        # save them in a dictionary
        batch_stats = {
                       "train_avg_mean_sim": avg_mean_sample_sims,
                       "train_avg_min_sim": avg_min_sample_sims,
                       "train_avg_max_sim": avg_max_sample_sims,
                       "train_avg_std_sim": avg_std_sample_sims,
                       }

    batch_loss_obj = loss_function.forward(g_x)

    batch_loss = batch_loss_obj.item()

    # perform the backpropagation
    batch_loss_obj.backward()

    if optimizer_step:
        # update the weights
        optimizer.step()

    if batch_stats:
        return batch_loss, batch_stats
    
    return batch_loss


def train_per_epoch(model: SimClrModel, 
                dataloader: DataLoader,
                loss_function: SimClrLoss,
                optimizer: torch.optim.Optimizer, 
                scheduler: Optional[torch.optim.lr_scheduler.LRScheduler], 
                epoch_index: int,
                device: str,
                log_per_batch: Union[int, float],
                accumulate_grads: int = 1,
                batch_stats:bool=False,
                use_wandb:bool=True
                ) -> dict[str, float]:

    if isinstance(log_per_batch, float):
        num_batches = len(dataloader)
        log_per_batch = int(math.ceil(num_batches * log_per_batch))

    # set the model to the train mode
    model.to(device=device)
    model.train()

    # define a function to save the average loss per epoch
    epoch_train_loss = 0
    
    num_batches = len(dataloader)


    for batch_index, (x1, x2) in tqdm(enumerate(dataloader, start=1), desc=f'training batch at epoch {epoch_index }'): 
        # to make the gradient accumulation work

        # 1. the optimizer.zero_grad() method should be called with batch indices such that batch_index % acc_grad == 1 (or no gradient accumulation at all (acc_grad == 1))

        # 2. the optimizer.step() should be called with batch_indices divisbly by acc_grad or the very last batch
 
        # the batch index should be 1-indexed not 0-indexed for the implementation above to work
        
        batch_train_res = train_per_batch(model=model, 
                                            x1_batch=x1, 
                                            x2_batch=x2, 
                                            loss_function=loss_function,
                                            optimizer=optimizer, 
                                            optimizer_zero_grad=((accumulate_grads == 1) or (batch_index % accumulate_grads) == 1),
                                            optimizer_step=((batch_index % accumulate_grads == 0) or (batch_index == num_batches)),
                                            device=device, 
                                            batch_stats=batch_stats)

        if batch_stats:
            batch_train_loss = batch_train_res[0]
        else:
            batch_train_loss = batch_train_res


        # log the batch loss depending on the batch index
        if batch_index % log_per_batch == 0 and use_wandb:
            log_dict = {"train_epoch": epoch_index, "train_loss": batch_train_loss} 
            
            if batch_stats:
               log_dict.update(batch_train_res[1]) 
            
            wandb.log(log_dict)                

        epoch_train_loss += batch_train_loss

    # make sure to call the scheduler to update the learning rate
    if scheduler is not None:
        scheduler.step()

    # make sure to average the metric
    epoch_train_loss = epoch_train_loss / len(dataloader)

    # log the metrics
    if use_wandb:
        wandb.log({
                "train_epoch": epoch_index, 
                "train_loss": epoch_train_loss
                })

    return {"loss": epoch_train_loss}

def validation_per_batch(model: SimClrModel, 
                        x1_batch: torch.Tensor,
                        x2_batch: torch.Tensor,
                        loss_function: nn.Module,
                     ):

    device = pu.get_module_device(model)
    # make sure to stack the two batches into a single batch 
    x = torch.cat([x1_batch, x2_batch]).to(device)

    with torch.no_grad():
        # forward pass through the model
        _, g_x = model.forward(x)


        if batch_stats:
            g_x_copy = g_x.detach() # a copy of the model embeddings without gradients
            # compute the similarities between the
            similarities = CosineSim().forward(g_x_copy, g_x_copy) 

            avg_mean_sample_sims = torch.mean(similarities.mean(dim=1)).item()
            avg_min_sample_sims = torch.mean(similarities.min(dim=1)[0]).item()

            # omit the diagonal entries since they are '1's and simply clutter the avg_max_sample_sims variable
            avg_max_sample_sims = torch.mean((similarities * (similarities != 1)).max(dim=1)[0]).item()
            avg_std_sample_sims = torch.mean(similarities.std(dim=1)).item()

            # save them in a dictionary
            batch_stats = {
                        "val_avg_mean_sim": avg_mean_sample_sims,
                        "val_avg_min_sim": avg_min_sample_sims,
                        "val_avg_max_sim": avg_max_sample_sims,
                        "val_avg_std_sim": avg_std_sample_sims,
                        }

        batch_loss_obj = loss_function.forward(g_x)

        batch_loss = batch_loss_obj.item()

    if batch_stats:
        return batch_loss, batch_stats
    
    return batch_loss

def validation_per_epoch(model: SimClrModel,
                        dataloader: DataLoader,
                        loss_function: nn.Module,
                        epoch_index: int,
                        device: str, 
                        log_per_batch: Union[float, int],
                        use_wandb:bool=True,
                        batch_stats:bool=False) -> dict[str, float]:

    if isinstance(log_per_batch, float):
        num_batches = len(dataloader)
        log_per_batch = int(math.ceil(num_batches * log_per_batch))

    epoch_val_loss = 0

    model = model.to(device=device)
    model.eval()

    for batch_index, (x1, x2) in tqdm(enumerate(dataloader, start=1), desc=f'validation batch for epoch {epoch_index}'):
        batch_val_res = validation_per_batch(model=model, 
                                        x1_batch=x1, 
                                        x2_batch=x2, 
                                        loss_function=loss_function)

        if batch_stats:
            batch_val_loss = batch_val_res[0]
        else:
            batch_val_loss = batch_val_res

        # log the batch loss depending on the batch index
        if batch_index % log_per_batch == 0 and use_wandb:
            log_dict = {"val_epoch": epoch_index, "batch_val_loss": batch_val_loss} 
            
            if batch_stats:
               log_dict.update(batch_val_res[1]) 
            wandb.log(log_dict)                

        epoch_val_loss += batch_val_loss

    # average the validation loss
    epoch_val_loss /= len(dataloader)

    if use_wandb:
        wandb.log({"epoch": epoch_index, "val_loss": epoch_val_loss})

    return {"loss": epoch_val_loss}
