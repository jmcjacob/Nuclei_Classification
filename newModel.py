import os
import time
import keras
import pickle
import numpy as np
import tensorflow as tf
from generator import ImageLoader
import sklearn.metrics as metrics


class Model:
    def __init__(self, config):
        """ Initialiser for the Model class.
        :param config: A ArgumentParser object
        """

        # Resets the Tensorflow computational graph.
        tf.reset_default_graph()

        # Sets the class variables from the config file.
        self.input_shape = [config.input_height, config.input_width, config.input_channels]
        self.num_classes = config.num_classes
        self.verbose = config.verbose
        self.config = config

        # Creates the classification model
        self.model = self.create_model()
        self.log("Model has been created\n")
        self.log(self.model.summary())

    def __copy__(self):
        """ Resets the model and returns a copy of the model.
        :return: A reset copy of the reset Model
        """

        tf.reset_default_graph()
        return Model(self.config)

    def log(self, message):
        """ Function to handle printing and logging of messages.
        :param message: String of message to be printed and logged.
        """

        if self.config.verbose:
            print(message)
        if self.config.log_file != '':
            print(message, file=open(self.config.log_file, 'a'))

    def create_model(self):
        """ Creates a CNN model for Classification.
        :return: A computational graph representing a CNN model for Classification.
        """

        # Alteration to Keras implementation of Dropout to be applied during prediction.
        class AlwaysDropout(keras.layers.Dropout):
            def call(self, inputs, training=None):
                if 0. < self.rate < 1.:
                    noise_shape = self._get_noise_shape(inputs)
                    return keras.backend.dropout(inputs, self.rate, noise_shape, seed=self.seed)
                return inputs

        # Block 1
        model = keras.models.Sequential()
        model.add(keras.layers.Conv2D(64, (3, 3), activation='relu', padding='same', input_shape=self.input_shape))
        model.add(keras.layers.BatchNormalization())
        model.add(keras.layers.Conv2D(64, (3, 3), padding='same', activation='relu'))
        model.add(keras.layers.BatchNormalization())
        model.add(keras.layers.MaxPool2D(padding='same'))
        if self.config.bayesian:
            model.add(AlwaysDropout(0.25))

        # Block 2
        model.add(keras.layers.Conv2D(128, (3, 3), padding='same', activation='relu'))
        model.add(keras.layers.BatchNormalization())
        model.add(keras.layers.Conv2D(128, (3, 3), padding='same', activation='relu'))
        model.add(keras.layers.BatchNormalization())
        model.add(keras.layers.MaxPool2D(padding='same'))
        if self.config.bayesian:
            model.add(AlwaysDropout(0.25))

        # Block 3
        model.add(keras.layers.Conv2D(256, (3, 3), padding='same', activation='relu'))
        model.add(keras.layers.BatchNormalization())
        model.add(keras.layers.Conv2D(256, (3, 3), padding='same', activation='relu'))
        model.add(keras.layers.BatchNormalization())
        model.add(keras.layers.Conv2D(256, (3, 3), padding='same', activation='relu'))
        model.add(keras.layers.BatchNormalization())
        model.add(keras.layers.MaxPool2D(padding='same'))
        if self.config.bayesian:
            model.add(AlwaysDropout(0.25))

        # Block 4
        model.add(keras.layers.Flatten())
        model.add(keras.layers.Dense(1024, activation='relu'))
        model.add(keras.layers.BatchNormalization())
        if self.config.bayesian:
            model.add(AlwaysDropout(0.25))
        model.add(keras.layers.Dense(1024, activation='relu'))
        model.add(keras.layers.BatchNormalization())
        model.add(keras.layers.Dense(self.num_classes, activation='softmax'))

        # Creates an optimiser object.
        optimiser = keras.optimizers.Adadelta(lr=self.config.learning_rate,
                                              rho=self.config.rho,
                                              epsilon=self.config.epsilon,
                                              decay=self.config.decay)

        # Creates the model with the optimiser and loss function.
        model.compile(optimizer=optimiser, loss='categorical_crossentropy', metrics=['accuracy'])
        return model

    def train(self, data, test=True, experiment="0"):
        """ The main training loop for the model.
        :param data: A dataset object.
        :param test: Boolean if the model should be tested.
        :param experiment: A string describing the experiment that is running.
        :return: Training metrics, accuracy, mean class accuray, recall, precision, f1-score and loss.
        """

        class EarlyStop(keras.callbacks.Callback):
            def __init__(self, min_epochs=0, batch=5, target=1., log_fn=print):
                super().__init__()
                self.val_losses = []
                self.train_losses = []
                self.min_epochs = min_epochs
                self.batch = batch
                self.target = target
                self.start_time = time.clock()
                self.log_fn = log_fn

            def on_epoch_end(self, epoch, logs=None):
                message = 'Epoch: ' + str(epoch + 1).zfill(4)
                message += ' Training Loss: {:.4f}'.format(logs.get('loss'))
                message += ' Validation Accuracy: {:.4f}'.format(logs.get('val_acc'))
                message += ' Validation Loss: {:.4f}'.format(logs.get('val_loss'))
                message += ' Time: {:.5f}s'.format(time.clock() - self.start_time)
                self.log_fn(message)
                self.val_losses.append(logs.get('val_loss'))
                self.train_losses.append(logs.get('loss'))
                if (epoch + 1) % self.batch == 0 and (epoch + 1) >= self.min_epochs:
                    g_loss = 100 * ((self.val_losses[-1] / min(self.val_losses[:-1])) - 1)
                    t_progress = 1000 * ((sum(self.train_losses[-(self.batch+1):-1]) /
                                          (2 * min(self.train_losses[--(self.batch+1):-1]))) - 1)
                    print('Training Progress: {:.4}'.format(g_loss / t_progress))
                    if g_loss / t_progress > self.target:
                        print('Stopped at epoch ' + str(epoch + 1))
                        self.model.stop_training = True

        # Loads the existing weights to the model.
        if self.config.model_tuning and self.config.mode != 'supervised' and os.path.isdir(self.config.model_path):
            self.model.load_weights(self.config.model_path + '/weights')
            self.log('Model Restored')

        gen = keras.preprocessing.image.ImageDataGenerator()

        train_gen = ImageLoader(data.train_x, data.train_y, self.config.data_dir, gen,
                                target_size=(27, 27), batch_size=self.config.batch_size)
        val_gen = ImageLoader(data.val_x, data.val_y, self.config.data_dir, gen,
                                target_size=(27, 27), shuffle=False)

        history = self.model.fit_generator(train_gen, verbose=0,
                                           epochs=self.config.max_epochs,
                                           validation_data=val_gen,
                                           callbacks=[EarlyStop(self.config.min_epochs,
                                                      self.config.batch_epochs,
                                                      self.config.training_threshold,
                                                      self.log)])

        with open('/History/' + experiment, 'wb') as file_pi:
            pickle.dump(history.history, file_pi)

        # if test:
        #     predictions = []
        #     test_gen = ImageLoader(data.val_x, data.val_y, self.config.data_dir, gen,
        #                           target_size=(27, 27), shuffle=False)
        #
        #     for i in range(self.config.bayesian_iterations):
        #         iterator = test_data.make_one_shot_iterator()
        #         next_batch = iterator.get_next()
        #         temp_predictions, labels = [], []
        #         for _ in range(test_steps):
        #             image_batch, label_batch = keras.backend.get_session().run(next_batch)
        #             temp_predictions += self.model.predict_on_batch(image_batch).tolist()
        #             labels += label_batch.tolist()
        #         predictions.append(temp_predictions)
        #     predictions = np.average(predictions, axis=0)
        #
        #     predicted_labels = []
        #     for i in range(0, len(predictions) - 1, self.config.cell_patches):
        #         predicted_labels.append(np.argmax(np.average(predictions[i:(i + self.config.cell_patches)])))
        #
        #     recall = metrics.recall_score(labels, predicted_labels, average='micro')
        #     precision = metrics.precision_score(labels, predicted_labels, average='micro')
        #     f1_score = metrics.f1_score(labels, predicted_labels, average='micro')
        #     cmat = metrics.confusion_matrix(labels, predicted_labels)
        #     accuracy = np.mean(cmat.diagonal() / cmat.sum(axis=1))
        #     accuracy_score = metrics.accuracy_score(labels, predicted_labels)
        #
        #     # Prints the calculated testing metrics.
        #     message = '\nModel trained with an Accuracy: {:.4f}'.format(accuracy_score)
        #     message += ' Mean Class Accuracy: {:.4f}'.format(accuracy)
        #     message += ' Recall: {:.4f}'.format(recall)
        #     message += ' Precision: {:.4f}'.format(precision)
        #     message += ' F1-Score: {:.4f}'.format(f1_score)
        #     self.log(message)

    def predict(self, data, method=np.average):
        """ Make cell predictions from the unlabelled dataset.
        :param data: A dataset object.
        :param method: A method for how to combine the predictions of each cell.
        :return: A list of predictions for each cell.
        """