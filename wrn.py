import numpy as np

import theano
import theano.tensor as T

import lasagne
from lasagne.layers import InputLayer
from lasagne.layers import DenseLayer, DropoutLayer
from lasagne.layers import batch_norm, BatchNormLayer, ExpressionLayer
from lasagne.layers import Conv2DLayer as ConvLayer
from lasagne.layers import ElemwiseSumLayer, NonlinearityLayer, GlobalPoolLayer
from lasagne.nonlinearities import rectify, sigmoid
from lasagne.init import HeNormal
from lasagne.layers import get_all_params, get_all_layers, get_output
from lasagne.regularization import regularize_layer_params
from lasagne.objectives import binary_crossentropy, binary_accuracy
from lasagne.updates import adam
from lasagne.layers import helper

from data_workers import OmniglotVerif, LFWVerif
from main import train, test, serialize, deserialize

import sys
sys.setrecursionlimit(10000)


import argparse


def residual_block(l, increase_dim=False, projection=True, first=False, filters=16):
	if increase_dim:
		first_stride = (2, 2)
	else:
		first_stride = (1, 1)
	if first:
		bn_pre_relu = l
	else:
		bn_pre_conv = BatchNormLayer(l)
		bn_pre_relu = NonlinearityLayer(bn_pre_conv, rectify)
	conv_1 = batch_norm(ConvLayer(bn_pre_relu, num_filters=filters, filter_size=(3,3), stride=first_stride, nonlinearity=rectify, pad='same', W=HeNormal(gain='relu')))
	dropout = DropoutLayer(conv_1, p=0.3)
	conv_2 = ConvLayer(dropout, num_filters=filters, filter_size=(3,3), stride=(1,1), nonlinearity=None, pad='same', W=HeNormal(gain='relu'))
	if increase_dim:
		projection = ConvLayer(l, num_filters=filters, filter_size=(1,1), stride=(2,2), nonlinearity=None, pad='same', b=None)
		block = ElemwiseSumLayer([conv_2, projection])
	elif first:
		projection = ConvLayer(l, num_filters=filters, filter_size=(1,1), stride=(1,1), nonlinearity=None, pad='same', b=None)
		block = ElemwiseSumLayer([conv_2, projection])
	else:
		block = ElemwiseSumLayer([conv_2, l])
	return block


parser = argparse.ArgumentParser(description="CLI for specifying hyper-parameters")
parser.add_argument("-n", "--expt-name", type=str, default="", help="experiment name(for logging purposes)")
parser.add_argument("--dataset", type=str, default="omniglot", help="omniglot/LFW")

parser.add_argument("--wrn-depth", type=int, default=3, help="the resnet has depth equal to 6d+12")
parser.add_argument("--wrn-width", type=int, default=2, help="width multiplier for each WRN block")

meta_data = vars(parser.parse_args())
meta_data["expt_name"] = "WRN_VERIF_" + meta_data["dataset"] + "_" + meta_data["expt_name"]

for md in meta_data.keys():
	print(md, meta_data[md])

expt_name = meta_data["expt_name"]
learning_rate = 1e-3
image_size = 64 # 32
batch_size = 128
meta_data["n_iter"] = n_iter = 100000
wrn_n = meta_data["wrn_depth"]
wrn_k = meta_data["wrn_width"]
meta_data["num_output"] = 2


print("... setting up the network")
n_filters = {0: 16, 1: 16 * wrn_k, 2: 32 * wrn_k, 3: 64 * wrn_k}

X = T.tensor4("input")
y = T.imatrix("target")

l_in = InputLayer(shape=(None, 1, image_size, image_size), input_var=X)
l = batch_norm(ConvLayer(l_in, num_filters=n_filters[0], filter_size=(3, 3), \
	stride=(1, 1), nonlinearity=rectify, pad='same', W=HeNormal(gain='relu')))
l = residual_block(l, first=True, filters=n_filters[1])
for _ in range(1, wrn_n):
	l = residual_block(l, filters=n_filters[1])
l = residual_block(l, increase_dim=True, filters=n_filters[2])
for _ in range(1, (wrn_n+2)):
	l = residual_block(l, filters=n_filters[2])
l = residual_block(l, increase_dim=True, filters=n_filters[3])
for _ in range(1, (wrn_n+2)):
	l = residual_block(l, filters=n_filters[3])

bn_post_conv = BatchNormLayer(l)
bn_post_relu = NonlinearityLayer(bn_post_conv, rectify)
avg_pool = GlobalPoolLayer(bn_post_relu)
dense_layer = DenseLayer(avg_pool, num_units=128, W=HeNormal(gain='relu'), nonlinearity=rectify)
dist_layer = ExpressionLayer(dense_layer, lambda I: T.abs_(I[:I.shape[0]/2] - I[I.shape[0]/2:]), output_shape='auto')
l_y = DenseLayer(dist_layer, num_units=1, nonlinearity=sigmoid)

prediction = get_output(l_y)
prediction_clean = get_output(l_y, deterministic=True)

loss = T.mean(binary_crossentropy(prediction, y))
accuracy = T.mean(binary_accuracy(prediction_clean, y))

all_layers = get_all_layers(l_y)
l2_penalty = 0.0001 * regularize_layer_params(all_layers, lasagne.regularization.l2)
loss = loss + l2_penalty

params = get_all_params(l_y, trainable=True)
updates = adam(loss, params, learning_rate=learning_rate)

meta_data["num_param"] = lasagne.layers.count_params(l_y)
print("number of parameters: ", meta_data["num_param"])

print("... compiling")
train_fn = theano.function(inputs=[X, y], outputs=loss, updates=updates)
val_fn = theano.function(inputs=[X, y], outputs=[loss, accuracy])
op_fn = theano.function([X], outputs=prediction_clean)


print("... loading dataset")
if meta_data["dataset"] == 'omniglot':
	worker = OmniglotOS(image_size=image_size, batch_size=batch_size)
elif meta_data["dataset"] == 'lfw':
	worker = LFWVerif(image_size=image_size, batch_size=batch_size)

meta_data, best_params = train(train_fn, val_fn, worker, meta_data, \
	get_params=lambda: helper.get_all_param_values(l_y))

if meta_data["testing"]:
	print("... testing")
	helper.set_all_param_values(l_y, best_params)
	meta_data = test(val_fn, worker, meta_data)

serialize(params, expt_name + '.params')
serialize(meta_data, expt_name + '.mtd')
serialize(op_fn, expt_name + '.opf')
