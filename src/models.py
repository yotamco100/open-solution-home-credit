from attrdict import AttrDict
from deepsense import neptune
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.externals import joblib
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from steppy.base import BaseTransformer
from toolkit.sklearn_transformers.models import SklearnClassifier
import xgboost as xgb
from keras.models import Sequential
from keras.layers import Dense
from keras.optimizers import Adam
from keras.models import Model
from keras.utils import plot_model

from .utils import get_logger

logger = get_logger()
ctx = neptune.Context()


class XGBoost(BaseTransformer):
    def __init__(self, **params):
        super().__init__()
        logger.info('initializing XGBoost...')
        self.params = params
        self.training_params = ['nrounds', 'early_stopping_rounds']
        self.evaluation_function = None

    @property
    def model_config(self):
        return AttrDict({param: value for param, value in self.params.items()
                         if param not in self.training_params})

    @property
    def training_config(self):
        return AttrDict({param: value for param, value in self.params.items()
                         if param in self.training_params})

    def fit(self,
            X, y,
            X_valid, y_valid,
            feature_names=None,
            feature_types=None,
            **kwargs):
        train = xgb.DMatrix(X,
                            label=y,
                            feature_names=feature_names,
                            feature_types=feature_types)
        valid = xgb.DMatrix(X_valid,
                            label=y_valid,
                            feature_names=feature_names,
                            feature_types=feature_types)

        evaluation_results = {}
        self.estimator = xgb.train(params=self.model_config,
                                   dtrain=train,
                                   evals=[(train, 'train'), (valid, 'valid')],
                                   evals_result=evaluation_results,
                                   num_boost_round=self.training_config.nrounds,
                                   early_stopping_rounds=self.training_config.early_stopping_rounds,
                                   verbose_eval=self.model_config.verbose,
                                   feval=self.evaluation_function)
        return self

    def transform(self, X, y=None, feature_names=None, feature_types=None, **kwargs):
        X_DMatrix = xgb.DMatrix(X,
                                label=y,
                                feature_names=feature_names,
                                feature_types=feature_types)
        prediction = self.estimator.predict(X_DMatrix)
        return {'prediction': prediction}

    def load(self, filepath):
        self.estimator = xgb.Booster(params=self.model_config)
        self.estimator.load_model(filepath)
        return self

    def persist(self, filepath):
        self.estimator.save_model(filepath)


class LightGBM(BaseTransformer):
    def __init__(self, name=None, **params):
        super().__init__()
        logger.info('initializing LightGBM...')
        self.params = params
        self.training_params = ['number_boosting_rounds', 'early_stopping_rounds']
        self.evaluation_function = None
        self.callbacks = callbacks(channel_prefix=name)

    @property
    def model_config(self):
        return AttrDict({param: value for param, value in self.params.items()
                         if param not in self.training_params})

    @property
    def training_config(self):
        return AttrDict({param: value for param, value in self.params.items()
                         if param in self.training_params})

    def fit(self,
            X,
            y,
            X_valid,
            y_valid,
            feature_names='auto',
            categorical_features='auto',
            **kwargs):
        evaluation_results = {}

        self._check_target_shape_and_type(y, 'y')
        self._check_target_shape_and_type(y_valid, 'y_valid')
        y = self._format_target(y)
        y_valid = self._format_target(y_valid)

        logger.info('LightGBM, train data shape        {}'.format(X.shape))
        logger.info('LightGBM, validation data shape   {}'.format(X_valid.shape))
        logger.info('LightGBM, train labels shape      {}'.format(y.shape))
        logger.info('LightGBM, validation labels shape {}'.format(y_valid.shape))

        data_train = lgb.Dataset(data=X,
                                 label=y,
                                 feature_name=feature_names,
                                 categorical_feature=categorical_features,
                                 **kwargs)
        data_valid = lgb.Dataset(X_valid,
                                 label=y_valid,
                                 feature_name=feature_names,
                                 categorical_feature=categorical_features,
                                 **kwargs)

        self.estimator = lgb.train(self.model_config,
                                   data_train,
                                   feature_name=feature_names,
                                   categorical_feature=categorical_features,
                                   valid_sets=[data_train, data_valid],
                                   valid_names=['data_train', 'data_valid'],
                                   evals_result=evaluation_results,
                                   num_boost_round=self.training_config.number_boosting_rounds,
                                   early_stopping_rounds=self.training_config.early_stopping_rounds,
                                   verbose_eval=self.model_config.verbose,
                                   feval=self.evaluation_function,
                                   callbacks=self.callbacks,
                                   **kwargs)
        return self

    def transform(self, X, **kwargs):
        prediction = self.estimator.predict(X)
        return {'prediction': prediction}

    def load(self, filepath):
        self.estimator = joblib.load(filepath)
        return self

    def persist(self, filepath):
        joblib.dump(self.estimator, filepath)

    def _check_target_shape_and_type(self, target, name):
        if not any([isinstance(target, obj_type) for obj_type in [pd.Series, np.ndarray, list]]):
            raise TypeError(
                '"target" must be "numpy.ndarray" or "Pandas.Series" or "list", got {} instead.'.format(type(target)))
        try:
            assert len(target.shape) == 1, '"{}" must be 1-D. It is {}-D instead.'.format(name,
                                                                                          len(target.shape))
        except AttributeError:
            print('Cannot determine shape of the {}. '
                  'Type must be "numpy.ndarray" or "Pandas.Series" or "list", got {} instead'.format(name,
                                                                                                     type(target)))

    def _format_target(self, target):

        if isinstance(target, pd.Series):
            return target.values
        elif isinstance(target, np.ndarray):
            return target
        elif isinstance(target, list):
            return np.array(target)
        else:
            raise TypeError(
                '"target" must be "numpy.ndarray" or "Pandas.Series" or "list", got {} instead.'.format(type(target)))


def get_sklearn_classifier(ClassifierClass, normalize, **kwargs):
    class SklearnBinaryClassifier(SklearnClassifier):
        def transform(self, X, y=None, target=1, **kwargs):
            prediction = self.estimator.predict_proba(X)[:, target]
            return {SklearnClassifier.RESULT_KEY: prediction}

    if normalize:
        return SklearnBinaryClassifier(Pipeline([('standarizer', StandardScaler()),
                                                 ('classifier', ClassifierClass(**kwargs))]))

    return SklearnBinaryClassifier(ClassifierClass(**kwargs))


def callbacks(channel_prefix):
    neptune_monitor = neptune_monitor_lgbm(channel_prefix)
    return [neptune_monitor]


def neptune_monitor_lgbm(channel_prefix=''):
    def callback(env):
        for name, loss_name, loss_value, _ in env.evaluation_result_list:
            if channel_prefix != '':
                channel_name = '{}_{}_{}'.format(channel_prefix, name, loss_name)
            else:
                channel_name = '{}_{}'.format(name, loss_name)
            ctx.channel_send(channel_name, x=env.iteration, y=loss_value)

    return callback


class neural_network():
    """
    A Keras-based Artificial Neural Network.
    Currently, this is just a framework built for easy model-building.
    """
    def __init__(self, input_size, output_size):
        """
        A neural network constructor. Recieves the input size and output size, outputs a neural_network object.
        """
        self.input_size = input_size
        self.output_size = output_size
        self.learning_rate = 1
        self.model = self._build_model()
        
    def _build_model(self):
        """
        Builds the model, runs on constructor call.
        TODO: Add Model architecture.
        """
        pass
        
    def load(self, name):
        """ Saves the model's weights to an h5 file."""
        self.model.load_weights(name)

    def save(self, name):
        """ Loads the model's weights to an h5 file. """
        self.model.save_weights(name)
