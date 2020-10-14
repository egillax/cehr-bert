from abc import ABC, abstractmethod
import os
from pathlib import Path
import pandas as pd

import tensorflow as tf
from tensorflow.keras.models import Model

from utils.logging_utils import *
from utils.model_utils import log_function_decorator, create_folder_if_not_exist, \
    save_training_history
from models.loss_schedulers import CosineLRSchedule


class AbstractModel(ABC):
    def __init__(self, *args, **kwargs):
        super().__init__()
        self._model = self._create_model(*args, **kwargs)

    @abstractmethod
    def _create_model(self, *args, **kwargs) -> Model:
        pass

    @abstractmethod
    def train_model(self, *args, **kwargs):
        pass

    @abstractmethod
    def eval_model(self, *args, **kwargs):
        pass

    @abstractmethod
    def get_model_folder(self):
        pass

    def get_model_metrics_folder(self):
        return create_folder_if_not_exist(self.get_model_folder(), 'metrics')

    def get_model_history_folder(self):
        return create_folder_if_not_exist(self.get_model_folder(), 'history')

    @classmethod
    def get_logger(cls):
        return logging.getLogger(cls.__name__)

    def __str__(self):
        return str(self.__class__.__name__)


class AbstractConceptEmbeddingTrainer(AbstractModel):
    min_num_of_concepts = 5

    def __init__(self,
                 training_data_parquet_path: str,
                 model_path: str,
                 batch_size: int,
                 epochs: int,
                 learning_rate: float,
                 tf_board_log_path: str = None,
                 shuffle_training_data: bool = True,
                 *args, **kwargs):

        self._training_data_parquet_path = training_data_parquet_path
        self._model_path = model_path
        self._tf_board_log_path = tf_board_log_path
        self._batch_size = batch_size
        self._epochs = epochs
        self._learning_rate = learning_rate
        self._shuffle_training_data = shuffle_training_data
        self._training_data = self._load_training_data()

        # shuffle the training data
        if self._shuffle_training_data:
            self._training_data = self._training_data.sample(frac=1).reset_index(drop=True)

        self._load_dependencies()

        super(AbstractConceptEmbeddingTrainer, self).__init__(*args, **kwargs)

        self.get_logger().info(
            f'training_data_parquet_path: {training_data_parquet_path}\n'
            f'model_path: {model_path}\n'
            f'batch_size: {batch_size}\n'
            f'epochs: {epochs}\n'
            f'learning_rate: {learning_rate}\n'
            f'tf_board_log_path: {tf_board_log_path}\n'
            f'shuffle_training_data: {shuffle_training_data}\n')

    @abstractmethod
    def _load_dependencies(self):
        pass

    @log_function_decorator
    def _load_training_data(self):
        if not os.path.exists(self._training_data_parquet_path):
            raise FileExistsError(f'{self._training_data_parquet_path} does not exist!')
        parquet = pd.read_parquet(self._training_data_parquet_path)
        return parquet

    @abstractmethod
    def create_dataset(self):
        """
        Prepare _training_data for the model such as tokenize concepts.
        :return:
        """
        pass

    def train_model(self):
        """
        Train the model and save the history metrics into the model folder
        :return:
        """
        dataset, steps_per_epoch = self.create_dataset()

        history = self._model.fit(dataset,
                                  steps_per_epoch=steps_per_epoch,
                                  epochs=self._epochs,
                                  callbacks=self._get_callbacks())
        save_training_history(history, self.get_model_history_folder())

    def _get_callbacks(self):
        tensor_board_callback = tf.keras.callbacks.TensorBoard(log_dir=self._tf_board_log_path)
        model_checkpoint = tf.keras.callbacks.ModelCheckpoint(filepath=self._model_path,
                                                              save_best_only=True, monitor='loss',
                                                              verbose=1)
        learning_rate_scheduler = tf.keras.callbacks.LearningRateScheduler(
            CosineLRSchedule(lr_high=self._learning_rate, lr_low=1e-8, initial_period=10),
            verbose=1)
        return [
            tensor_board_callback,
            model_checkpoint,
            learning_rate_scheduler
        ]

    def get_model_folder(self):
        """
        Infer the model folder from the property model_path
        :return:
        """
        return str(Path(self._model_path).parent)