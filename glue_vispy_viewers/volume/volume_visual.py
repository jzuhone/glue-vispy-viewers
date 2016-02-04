# This file implements a MultiVolumeVisual class that can be used to show
# multiple volumes simultaneously. It is derived from the original VolumeVisual
# class in vispy.visuals.volume, which is releaed under a BSD license included
# here:
#
# ===========================================================================
# Vispy is licensed under the terms of the (new) BSD license:
#
# Copyright (c) 2015, authors of Vispy
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# * Redistributions of source code must retain the above copyright
#   notice, this list of conditions and the following disclaimer.
# * Redistributions in binary form must reproduce the above copyright
#   notice, this list of conditions and the following disclaimer in the
#   documentation and/or other materials provided with the distribution.
# * Neither the name of Vispy Development Team nor the names of its
#   contributors may be used to endorse or promote products
#   derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS
# IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED
# TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
# PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER
# OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
# PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
# ===========================================================================
#
# This modified version is released under the BSD license given in the LICENSE
# file in this repository.


from vispy.gloo import Texture3D, TextureEmulated3D, VertexBuffer, IndexBuffer
from vispy.visuals import VolumeVisual, Visual
from vispy.visuals.shaders import Function, ModularProgram
from vispy.color import get_colormap
from vispy.scene.visuals import create_visual_node

import numpy as np

from collections import defaultdict
from .shaders import get_shaders


class MultiVolumeVisual(VolumeVisual):
    """
    Displays multiple 3D volumes simultaneously.

    Parameters
    ----------
    volumes : list of tuples
        The volumes to show. Each tuple should contain three elements: the data
        array, the clim values, and the colormap to use. The clim values should
        be either a 2-element tuple, or None.
    relative_step_size : float
        The relative step size to step through the volume. Default 0.8.
        Increase to e.g. 1.5 to increase performance, at the cost of
        quality.
    emulate_texture : bool
        Use 2D textures to emulate a 3D texture. OpenGL ES 2.0 compatible,
        but has lower performance on desktop platforms.
    n_volume_max : int
        Absolute maximum number of volumes that can be shown.
    """

    def __init__(self, n_volume_max=10, threshold=None, relative_step_size=0.8,
                 emulate_texture=False):

        # Choose texture class
        tex_cls = TextureEmulated3D if emulate_texture else Texture3D

        self._n_volume_max = n_volume_max
        self._initial_shape = True
        self._vol_shape = (10, 10, 10)
        self._need_vertex_update = True

        # Create OpenGL program
        vert_shader, frag_shader = get_shaders(n_volume_max)
        
        # We deliberately don't use super here because we don't want to call
        # VolumeVisual.__init__
        Visual.__init__(self, vcode=vert_shader, fcode=frag_shader)

        # Create gloo objects
        self._vertices = VertexBuffer()
        self._texcoord = VertexBuffer(
            np.array([
                [0, 0, 0],
                [1, 0, 0],
                [0, 1, 0],
                [1, 1, 0],
                [0, 0, 1],
                [1, 0, 1],
                [0, 1, 1],
                [1, 1, 1],
            ], dtype=np.float32))

        self.textures = []
        for i in range(n_volume_max):

            # Set up texture object
            self.textures.append(tex_cls(self._vol_shape, interpolation='linear',
                                          wrapping='clamp_to_edge'))

            # Pass texture object and default colormap to shader program
            self.shared_program['u_volumetex_{0}'.format(i)] = self.textures[i]
            self.shared_program.frag['cmap{0:d}'.format(i)] = Function(get_colormap('grays').glsl_map)

            # Make sure all textures are disbaled
            self.shared_program['u_enabled_{0}'.format(i)] = 0
            self.shared_program['u_weight_{0}'.format(i)] = 1

        self.shared_program['a_position'] = self._vertices
        self.shared_program['a_texcoord'] = self._texcoord
        self.shared_program['u_shape'] = self._vol_shape[::-1]
        self._draw_mode = 'triangle_strip'
        self._index_buffer = IndexBuffer()

        self.shared_program.frag['sampler_type'] = self.textures[0].glsl_sampler_type
        self.shared_program.frag['sample'] = self.textures[0].glsl_sample

        # Only show back faces of cuboid. This is required because if we are
        # inside the volume, then the front faces are outside of the clipping
        # box and will not be drawn.
        self.set_gl_state('translucent', cull_face=False)

        self.relative_step_size = relative_step_size

        self.volumes = defaultdict(dict)

        try:
            self.freeze()
        except AttributeError:  # Older versions of VisPy
            pass

    @property
    def _free_slot_index(self):
        for i in range(self._n_volume_max):
            if self.shared_program['u_enabled_{0}'.format(i)] == 0:
                return i
        raise ValueError("No free slots")

    def set_volume(self, label, data, clim, cmap):

        if label in self.volumes:
            index = self.volumes[label]['index']
            print("Using existing slot: {0}".format(index))
        else:
            index = self._free_slot_index
            self.volumes[label]['index'] = index
            self.volumes[label]['data'] = data
            self.volumes[label]['clim'] = clim
            self.volumes[label]['cmap'] = cmap
            print("Using new slot: {0}".format(index))

        data = data.astype(np.float32)
        data -= clim[0]
        data /= clim[1] - clim[0]

        # Make Python 2/3-friendly
        if isinstance(cmap, str):
            cmap = get_colormap(cmap)

        self.shared_program['u_volumetex_{0:d}'.format(index)].set_data(data)
        self.shared_program['u_enabled_{0:d}'.format(index)] = 1
        self.shared_program.frag['cmap{0:d}'.format(index)] = Function(cmap.glsl_map)

        if self._initial_shape:
            self._vol_shape = data.shape
            self.shared_program['u_shape'] = data.shape[::-1]
            self._initial_shape = False
        elif data.shape != self._vol_shape:
            raise ValueError("Shape of arrays should be {0} instead of {1}".format(self._vol_shape, data.shape))

    def set_weight(self, label, weight):
        index = self.volumes[label]['index']
        self.shared_program['u_weight_{0:d}'.format(index)] = weight


MultiVolume = create_visual_node(MultiVolumeVisual)
