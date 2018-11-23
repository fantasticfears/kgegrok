"""Training function module."""

import data
import kgekit.data
import logging
import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
from utils import save_checkpoint, load_checkpoint, write_logging_data
import stats
import evaluation


def create_optimizer(optimizer_class, config, parameters):
    """return optimizer initialized with correct parameters."""
    if optimizer_class == optim.Adagrad:
        return optimizer_class(parameters, lr=config.alpha, lr_decay=config.lr_decay, weight_decay=config.weight_decay)
    elif optimizer_class == optim.Adadelta:
        return optimizer_class(parameters, lr=config.alpha)
    elif optimizer_class == optim.Adam:
        return optimizer_class(parameters, lr=config.alpha)
    else:
        return optimizer_class(parameters, lr=config.alpha)

def test(triple_source, config, model_class, pool):
    """Test config.resume model."""
    data_loader = data.create_dataloader(triple_source, config, collates_label=False, dataset_type=data.DatasetType.TESTING)
    model = nn.DataParallel(model_class(triple_source, config))
    load_checkpoint(config, model)
    ranker = kgekit.Ranker(triple_source.train_set, triple_source.valid_set, triple_source.test_set)

    if config.enable_cuda:
        model.cuda()

    logging.info('Testing starts')
    result = evaluation.predict_links(model, triple_source, config, data_loader, pool)

    stats.report_prediction_result(config, result, epoch=i_epoch, drawer=None)

    return model

def train_and_validate(triple_source, config, model_class, optimizer_class, pool, drawer=None, enable_validation=True):
    """Train and validates the dataset."""
    # Data loaders have many processes. Here it's main process.
    data_loader = data.create_dataloader(triple_source, config, model_class.require_labels())
    if enable_validation:
        valid_data_loader = data.create_dataloader(triple_source, config, collates_label=False, dataset_type=data.DatasetType.VALIDATION)
    model = nn.DataParallel(model_class(triple_source, config))
    # has to be here because https://discuss.pytorch.org/t/effect-of-calling-model-cuda-after-constructing-an-optimizer/15165/7
    if config.enable_cuda:
        model.cuda()
    optimizer = create_optimizer(optimizer_class, config, model.parameters())
    load_checkpoint(config, model, optimizer)

    if drawer is not None:
        drawer.create_plot(data.LOSS_FEATURE_KEY, stats.gen_drawer_option(config, "Loss value"))
        if enable_validation:
            stats.prepare_plot_validation_result(drawer, config)

    INDEX_OFFSET = 1
    for i_epoch in range(INDEX_OFFSET, config.epoches+INDEX_OFFSET, 1):
        model.train()
        logging.info('--------------------')
        logging.info('Training at epoch ' + str(i_epoch))
        logging.info('--------------------')

        loss_epoch = 0.0
        for i_batch, sample_batched in enumerate(data_loader):
            logging.info('Training batch ' + str(i_batch+INDEX_OFFSET) + "/" + str(len(data_loader)))
            batch, negative_batch = sample_batched
            batch = data.convert_triple_tuple_to_torch(data.get_triples_from_batch(batch), config)
            negative_batch = data.convert_triple_tuple_to_torch(data.get_triples_from_batch(negative_batch), config)
            loss = model.forward(batch, negative_batch)
            loss_sum = loss.sum()
            loss_sum.backward()
            optimizer.step()
            loss_epoch += loss_sum.data[0]

        if drawer is not None:
            drawer.append(data.LOSS_FEATURE_KEY, X=np.array([i_epoch], dtype='f'), Y=np.array([loss_epoch], dtype='f'))
        logging.info("Epoch " + str(i_epoch) + ": loss " + str(loss_epoch))

        if enable_validation:
            logging.info('Evaluation for epoch ' + str(i_epoch))
            result = evaluation.predict_links(model, triple_source, config, valid_data_loader, pool)
            stats.report_prediction_result(config, result, epoch=i_epoch, drawer=drawer)

        save_checkpoint({
            'epoch': i_epoch,
            'state_dict': model.state_dict(),
            'optimizer' : optimizer.state_dict(),
        }, "model_states/" + config.name + "/checkpoint.pth.tar", postfix_num=i_epoch)

    if drawer is not None:
        write_logging_data(drawer.dump_raw_data(), config)

    return model

def train(triple_source, config, model_class, optimizer_class, drawer):
    """Train the dataset."""
    train_and_validate(triple_source, config, model_class, optimizer_class, drawer, enable_validation=False)

def interactive_prediction(triple_source, entities, relations, config, model_class, generator):
    """prints prediction results according to config and generator inputs."""
    model = model_class(triple_source, config)
    load_checkpoint(config, model)
    model.eval()

    logging.info('Interactive prediction starts')
    for head, relation, tail in generator:
        logging.info('--------------------')
        logging.info('Prediction input ({}, {}, {})'.format(head, relation, tail))
        logging.info('--------------------')

        # determines which element to predict
        batch, prediction_type, triple_index = data.sieve_and_expand_triple(triple_source, entities, relations, head, relation, tail)
        batch = data.convert_triple_tuple_to_torch(batch, config)
        predicted = model.forward(batch)

        logging.info('Predicting {} for ({}, {}, {})'.format(repr(prediction_type), head, relation, tail))
        prediction_list = evaluation.evaluate_single_triple(predicted, prediction_type, triple_index, config, entities, relations)
        logging.info('Top {} predicted elements are:'.format(rank, filtered_rank, len(prediction_list)))
        for idx, prediction in prediction_list:
            logging.info('{}: {}'.format(idx, prediction))
