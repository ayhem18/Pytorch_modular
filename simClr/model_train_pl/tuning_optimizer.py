"""
This script contains the code for tuning the Pytorch Lightning module
"""
import os, pytorch_lightning as L


from typing import Union, Optional, Tuple, List
from pathlib import Path

from clearml import Task, Logger
from clearml.automation.optimization import HyperParameterOptimizer

from clearml.automation import DiscreteParameterRange, LogUniformParameterRange
from clearml.automation.optuna import OptimizerOptuna

from mypt.code_utilities import pytorch_utilities as pu, directories_and_files as dirf

from .set_ds import _set_data_tune
from .train import  _set_logging, _set_ckpnt_callback
from .simClrWrapper import ResnetSimClrWrapper
from .constants import (TRACK_PROJECT_NAME, TUNING_TASK_RUN_NAME) 
from .tuning_train import tune_template_task_function


def tune_main_function(
        train_data_folder:Union[str, Path],          
        val_data_folder:Optional[Union[str, Path]],
        dataset: str,

        num_jobs: int,
        num_epochs_per_job: int, 
        batch_size: int,

        parent_log_dir: Union[str, Path], # parent_log_dir where all the logs from the different sub-folders will be saved
        tune_exp_number: int,
        output_shape:Tuple[int, int]=None, 
        num_warmup_epochs:int=10,
        val_per_epoch: int = 3,
        seed:int = 69,
    ):

    tune_template_task_function(train_data_folder=train_data_folder,
                                val_data_folder=val_data_folder,
                                dataset=dataset,
                                num_epochs=num_epochs_per_job,
                                batch_size=batch_size,
                                parent_log_dir=parent_log_dir,
                                tune_exp_number=tune_exp_number,
                                output_shape=output_shape,
                                num_warmup_epochs=num_warmup_epochs,
                                val_per_epoch=val_per_epoch,
                                seed=seed,
                                set_up=True # make sure to call it once with setup so that the task is created with the different parameters needed
                                )

    optimization_task = Task.init( 
                        project_name=TRACK_PROJECT_NAME, 
                        task_name=f'{TUNING_TASK_RUN_NAME}_{tune_exp_number}_hp_optimization',
                        task_type=Task.TaskTypes.optimizer,
                        reuse_last_task_id=False, 

                        auto_connect_arg_parser=False, 
                        auto_connect_frameworks=False, 
                        auto_connect_streams=False
                    )   
    
    # there should be some way to connect the optimization task to the optimizer
    # the one implicitly suggested by the tutorial: https://github.com/allegroai/clearml/blob/master/examples/optimization/hyper-parameter-optimization/hyper_parameter_optimizer.py
    # is by creating a dictionary object with the template_task_id, connecting it to the 'optimization_task' and using the same value (through the dictionary) in the HPOptimizer instance.

    template_task_id = Task.get_task(project_name=TRACK_PROJECT_NAME, 
                                    task_name=f'{TUNING_TASK_RUN_NAME}_{tune_exp_number}').id # the template task should be already created at this point
        
    args = {"template_task_id": template_task_id}

    # connect the 'optimization_task' with the 'args' variable
    optimization_task.connect(args)

    # define an optimizer
    tune_optimizer = HyperParameterOptimizer(
        base_task_id=args['template_task_id'], # calling the template_task_id through the 'args' seems like the only way to connect the hp optimizer to the 'optimization_task'

        # adding the 'General/' prefix, according to the tutorial: 
        # https://github.com/allegroai/clearml/blob/master/examples/optimization/hyper-parameter-optimization/hyper_parameter_optimizer.py

        hyper_parameters=[
            LogUniformParameterRange(name='General/dropout', min_value='-3', max_value=0),  # from e^{-3} to 1
            DiscreteParameterRange(name='General/num_fc_layers', values=list(range(2, 6))), # from 2 to 5 fully connected layers 
            DiscreteParameterRange(name='General/set_up', values=[False]), # always  use False for 'set_up'
        ],

        # this is the objective metric we want to maximize/minimize
        objective_metric_title='val_epoch_loss',
        objective_metric_series='val_epoch_loss',
        # minimize the val_epoch_loss
        objective_metric_sign='min',
        max_number_of_concurrent_tasks=1, #humble starts

        optimizer_class=OptimizerOptuna,
        # If specified all Tasks created by the HPO process will be created under the `spawned_project` project
        spawn_project=None,  
        # If specified only the top K performing Tasks will be kept, the others will be automatically archived
        save_top_k_tasks_only=3,  # 5,

        max_iteration_per_job=1, # since I am specifying the number of epochs per job (I think each job should be simply )
        total_max_jobs=num_jobs
    )

    # run the optimizer
    tune_optimizer.start_locally()

    # make sure to call the 'wait' function as the experiments are running on background threads 
    # and not the main process...
    tune_optimizer.wait()

    # not setting the time limit for now
    tune_optimizer.stop()
