# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

# coding: utf-8
# pylint: disable= arguments-differ,missing-docstring
"""SENet, implemented in Gluon."""
from __future__ import division

__all__ = ['SENet', 'SEBlock', 'get_senet',
           'senet_52', 'senet_103', 'senet_154']

import os
import math
from mxnet import cpu
from mxnet.gluon import nn
from mxnet.gluon.block import HybridBlock


class SEBlock(HybridBlock):
    r"""SEBlock from `"Aggregated Residual Transformations for Deep Neural Network"
    <http://arxiv.org/abs/1611.05431>`_ paper.

    Parameters
    ----------
    cardinality: int
        Number of groups
    bottleneck_width: int
        Width of bottleneck block
    stride : int
        Stride size.
    downsample : bool, default False
        Whether to downsample the input.
    """

    def __init__(self, channels, cardinality, bottleneck_width, stride,
                 downsample=False, **kwargs):
        super(SEBlock, self).__init__(**kwargs)
        D = int(math.floor(channels * (bottleneck_width / 64)))
        group_width = cardinality * D

        self.body = nn.HybridSequential(prefix='')
        self.body.add(nn.Conv2D(group_width//2, kernel_size=1, use_bias=False))
        self.body.add(nn.BatchNorm())
        self.body.add(nn.Activation('relu'))
        self.body.add(nn.Conv2D(group_width, kernel_size=3, strides=stride, padding=1,
                                use_bias=False))
        self.body.add(nn.BatchNorm())
        self.body.add(nn.Activation('relu'))
        self.body.add(nn.Conv2D(channels * 4, kernel_size=1, use_bias=False))
        self.body.add(nn.BatchNorm())

        self.se = nn.HybridSequential(prefix='')
        self.se.add(nn.Dense(channels // 4, use_bias=False))
        self.se.add(nn.Activation('relu'))
        self.se.add(nn.Dense(channels * 4, use_bias=False))
        self.se.add(nn.Activation('sigmoid'))

        if downsample:
            self.downsample = nn.HybridSequential(prefix='')
            self.downsample.add(nn.Conv2D(channels * 4, kernel_size=1, strides=stride,
                                          use_bias=False))
            self.downsample.add(nn.BatchNorm())
        else:
            self.downsample = None

    def hybrid_forward(self, F, x):
        residual = x

        x = self.body(x)

        w = F.contrib.AdaptiveAvgPooling2D(x, output_size=1)
        w = self.se(w)
        x = F.broadcast_mul(x, w.expand_dims(axis=2).expand_dims(axis=2))

        if self.downsample:
            residual = self.downsample(residual)

        x = F.Activation(x + residual, act_type='relu')
        return x


# Nets
class SENet(HybridBlock):
    r"""ResNext model from
    `"Aggregated Residual Transformations for Deep Neural Network"
    <http://arxiv.org/abs/1611.05431>`_ paper.

    Parameters
    ----------
    layers : list of int
        Numbers of layers in each block
    cardinality: int
        Number of groups
    bottleneck_width: int
        Width of bottleneck block
    classes : int, default 1000
        Number of classification classes.
    """
    def __init__(self, layers, cardinality, bottleneck_width,
                 classes=1000, **kwargs):
        super(SENet, self).__init__(**kwargs)
        self.cardinality = cardinality
        self.bottleneck_width = bottleneck_width
        channels = 64

        with self.name_scope():
            self.features = nn.HybridSequential(prefix='')
            self.features.add(nn.Conv2D(channels, 3, 2, 1, use_bias=False))
            self.features.add(nn.Conv2D(channels, 3, 1, 1, use_bias=False))
            self.features.add(nn.Conv2D(channels, 3, 1, 1, use_bias=False))
            self.features.add(nn.BatchNorm())
            self.features.add(nn.Activation('relu'))
            self.features.add(nn.MaxPool2D(3, 2, 1))

            for i, num_layer in enumerate(layers):
                stride = 1 if i == 0 else 2
                self.features.add(self._make_layer(channels, num_layer, stride, i+1))
                channels *= 2
            self.features.add(nn.AvgPool2D(7))
            self.features.add(nn.Dropout(0.2))

            self.output = nn.Dense(classes)

    def _make_layer(self, channels, num_layers, stride, stage_index):
        layer = nn.HybridSequential(prefix='stage%d_'%stage_index)
        with layer.name_scope():
            layer.add(SEBlock(channels, self.cardinality, self.bottleneck_width,
                              stride, True, prefix=''))
            for _ in range(num_layers-1):
                layer.add(SEBlock(channels, self.cardinality, self.bottleneck_width,
                                  1, False, prefix=''))
        return layer

    # pylint: disable=unused-argument
    def hybrid_forward(self, F, x):
        x = self.features(x)
        x = self.output(x)

        return x


# Specification
resnext_spec = {50: [3, 4, 6, 3],
                101: [3, 4, 23, 3],
                152: [3, 8, 36, 3]}


# Constructor
def get_senet(num_layers, cardinality=64, bottleneck_width=4,
              pretrained=False, ctx=cpu(),
              root=os.path.join('~', '.mxnet', 'models'), **kwargs):
    r"""ResNext model from `"Aggregated Residual Transformations for Deep Neural Network"
    <http://arxiv.org/abs/1611.05431>`_ paper.

    Parameters
    ----------
    num_layers : int
        Numbers of layers. Options are 50, 101.
    cardinality: int
        Number of groups
    bottleneck_width: int
        Width of bottleneck block
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    ctx : Context, default CPU
        The context in which to load the pretrained weights.
    root : str, default '~/.mxnet/models'
        Location for keeping the model parameters.
    """
    assert num_layers in resnext_spec, \
        "Invalid number of layers: %d. Options are %s"%(
            num_layers, str(resnext_spec.keys()))
    layers = resnext_spec[num_layers]
    net = SENet(layers, cardinality, bottleneck_width, **kwargs)
    if pretrained:
        from ..model_store import get_model_file
        net.load_params(get_model_file('resnext%d_%dx%d'%(num_layers, cardinality,
                                                          bottleneck_width),
                                       root=root), ctx=ctx)
    return net

def senet_52(**kwargs):
    r"""ResNext50 32x4d model from
    `"Aggregated Residual Transformations for Deep Neural Network"
    <http://arxiv.org/abs/1611.05431>`_ paper.

    Parameters
    ----------
    cardinality: int
        Number of groups
    bottleneck_width: int
        Width of bottleneck block
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    ctx : Context, default CPU
        The context in which to load the pretrained weights.
    root : str, default '~/.mxnet/models'
        Location for keeping the model parameters.
    """
    return get_senet(50, 32, 4, **kwargs)

def senet_103(**kwargs):
    r"""ResNext50 32x4d model from
    `"Aggregated Residual Transformations for Deep Neural Network"
    <http://arxiv.org/abs/1611.05431>`_ paper.

    Parameters
    ----------
    cardinality: int
        Number of groups
    bottleneck_width: int
        Width of bottleneck block
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    ctx : Context, default CPU
        The context in which to load the pretrained weights.
    root : str, default '~/.mxnet/models'
        Location for keeping the model parameters.
    """
    return get_senet(101, 32, 4, **kwargs)

def senet_154(**kwargs):
    r"""ResNext50 32x4d model from
    `"Aggregated Residual Transformations for Deep Neural Network"
    <http://arxiv.org/abs/1611.05431>`_ paper.

    Parameters
    ----------
    cardinality: int
        Number of groups
    bottleneck_width: int
        Width of bottleneck block
    pretrained : bool, default False
        Whether to load the pretrained weights for model.
    ctx : Context, default CPU
        The context in which to load the pretrained weights.
    root : str, default '~/.mxnet/models'
        Location for keeping the model parameters.
    """
    return get_senet(152, **kwargs)
