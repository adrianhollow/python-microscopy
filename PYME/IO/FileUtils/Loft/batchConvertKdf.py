#!/usr/bin/python

###############
# batchConvertKdf.py
#
# Copyright David Baddeley, 2012
# d.baddeley@auckland.ac.nz
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

#
################
import sys
import os

import KdfStackToHdf5


if __name__ == '__main__':
    pixelsize=0.09

    fns = sys.argv[1:]

    for inFile in fns:
        outFile = os.path.splitext(inFile)[0] + '.h5'
        KdfStackToHdf5.convertFile(inFile, outFile, pixelsize=pixelsize)