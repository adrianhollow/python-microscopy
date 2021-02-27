import numpy as np
from PYME.IO.MetaDataHandler import get_camera_roi_origin, get_camera_physical_roi_origin, load_json, NestedClassMDHandler

import os
import glob
import collections
import time
import six
import tempfile
import logging

logger = logging.getLogger(__name__)

CacheEntry = collections.namedtuple('CacheEntry', ['data', 'saved'])

class TileCache(object):
    def __init__(self, max_size=1000):
        self._max_size = max_size
        self._cache = {}
        #self._cache_size=0
        self._cache_keys = []
        
    def _load(self, filename):
        return np.load(filename)
        
    def load(self, filename):
        try:
            return self._cache[filename].data
        except KeyError:
            data = self._load(filename)
            self._add(filename, data, saved=True)
            return data
        
    def save(self, filename, data):
        self._add(filename, data, saved=False)
        
    def _save(self, filename, data):
        dirname = os.path.split(filename)[0]
        if not os.path.exists(dirname):
            os.makedirs(dirname)
            
        np.save(filename, data)
        
    def _save_entry(self, filename):
        item = self._cache[filename]
        if not item.saved:
            self._save(filename, item.data)
            self._cache[filename] = CacheEntry(data=item.data, saved=True)
            
        
    def _add(self, filename, data, saved=True):
        if filename in self._cache_keys:
            # replace existing item
            # we are going to be a bit sneaky, and remove the key before re-adding it later. This will move the key to the
            # back of the list, making it the most recently accessed (and least likely to be popped to make way for new data)
            self._cache_keys.remove(filename)
            
        if len(self._cache_keys) >= self._max_size:
            # adding item would make us too large, pop oldest entry from our cache
            fn = self._cache_keys.pop(0) #remove oldest item
            self._drop(fn)
        
        self._cache_keys.append(filename)
        self._cache[filename] = CacheEntry(data=data, saved=saved)
            
    def _drop(self, filename):
        item = self._cache.pop(filename)
        if not item.saved:
            self._save(filename, item.data)
            
    def flush(self):
        for filename, item  in list(self._cache.items()):
            if not item.saved:
                self._save(filename, item.data)
                self._cache[filename] = CacheEntry(data=item.data, saved=True)
                
    def remove(self, filename):
        if filename in self._cache_keys:
            self._cache_keys.remove(filename)
            self._cache.pop(filename)
            
        if os.path.exists(filename):
            os.remove(filename)
                
    def purge(self):
        self.flush()
        self._cache_keys = []
        self._cache = {}
        
    def exists(self, filename):
        return (filename in self._cache_keys) or os.path.exists(filename)
    
    
class PZFTileCache(TileCache):
    def _save(self, filename, data):
        from PYME.IO import PZFFormat
        dirname = os.path.split(filename)[0]
        if not os.path.exists(dirname):
            os.makedirs(dirname)
        
        with open(filename, 'wb') as f:
            f.write(PZFFormat.dumps(data.astype('float32')))
    
    def _load(self, filename):
        from PYME.IO import PZFFormat
        with open(filename, 'rb') as f:
            return PZFFormat.loads(f.read())[0].squeeze()
    
class TileIO(object):
    def get_tile(self, layer, x, y):
        raise NotImplementedError
    
    def save_tile(self, layer, x, y, data):
        raise  NotImplementedError
    
    def delete_tile(self, layer, x, y):
        raise  NotImplementedError
    
    def tile_exists(self, layer, x, y):
        raise NotImplementedError
    
    def get_layer_tile_coords(self, layer):
        raise NotImplementedError
    
    def flush(self):
        pass

class NumpyTileIO(TileIO):
    def __init__(self, base_dir, suff='img'):
        self.base_dir = base_dir
        self.suff = suff + '.npy'
        
        self.pattern = os.sep.join([self.base_dir, '%d', '%03d', '%03d_%03d_' + self.suff])
        
        self._tilecache = TileCache()
        self._coords = {}
    
    def _filename(self, layer, x, y):
        return self.pattern % (layer, x, x, y)
        #return os.path.join(self.base_dir, '%d' % layer, '%03d' % x, '%03d_%03d_%s' % (x, y, self.suff))
    
    def get_tile(self, layer, x, y):
        try:
            return self._tilecache.load(self._filename(layer, x, y))
        except IOError:
            return None
        
    def save_tile(self, layer, x, y, data):
        self._check_layer_tile_coords(layer)
        self._tilecache.save(self._filename(layer, x, y), data)
        
        if not (x,y) in self._coords[layer]:
            self._coords[layer].append((x,y))
        
    def delete_tile(self, layer, x, y):
        self._check_layer_tile_coords(layer)
        self._tilecache.remove(self._filename(layer, x, y))
        self._coords[layer].remove((x, y))
        
    def tile_exists(self, layer, x, y):
        #return (x, y) in self._coords[layer]
        return self._tilecache.exists(self._filename(layer, x, y))
        
    def _check_layer_tile_coords(self, layer=0):
        if not layer in self._coords.keys():
            self._update_layer_tile_coords(layer)
    
    def _update_layer_tile_coords(self, layer=0):
        tiles = []
        for xdir in glob.glob(os.path.join(self.base_dir, '%d' % layer, '*')):
            for fn in glob.glob(os.path.join(xdir, '*_%s' % self.suff)):
                tiles.append(tuple([int(s) for s in os.path.basename(fn).split('_')[:2]]))
                
        self._coords[layer] = tiles
        
    def get_layer_tile_coords(self, layer=0):
        self._check_layer_tile_coords(layer)
        return self._coords[layer]
    
    def flush(self):
        self._tilecache.flush()

class PZFTileIO(NumpyTileIO):
    def __init__(self, base_dir, suff='img'):
        self.base_dir = base_dir
        self.suff = suff + '.pzf'

        self.pattern = os.sep.join([self.base_dir, '%d', '%03d', '%03d_%03d_' + self.suff])
    
        self._tilecache = PZFTileCache()
        self._coords = {}
        

if six.PY2:
    def blob(data):
        return buffer(data)
else:
    #Py3k
    def blob(data):
        return bytes(data)

class SqliteTileIO(TileIO):
    def __init__(self, base_dir, suff='img'):
        import sqlite3
        
        self._conn = sqlite3.connect(os.path.join(base_dir, '%s.db' % suff))
        self._cur = self._conn.cursor()

        self._known_tables =[r[0] for r in self._cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        self._coords = {}
        
        
    def get_tile(self, layer, x, y):
        from PYME.IO import PZFFormat
        table = 'layer%d' % layer
        if not table in self._known_tables:
            return None
        
        self._cur.execute('SELECT data FROM layer%d WHERE x=? AND y=?' % layer, (x, y))
        r = self._cur.fetchone()
        if r is None:
            return None
        else:
            return PZFFormat.loads(r[0])[0].squeeze()
    
    def save_tile(self, layer, x, y, data):
        from PYME.IO import PZFFormat
        table = 'layer%d' % layer
        
        if not table in self._known_tables:
            self._cur.execute('CREATE TABLE %s (y INTEGER, x INTEGER, data BLOB)' % table)
            self._cur.execute('CREATE INDEX %s ON %s (x,y)' % ('idx_' + table, table))
            self._known_tables.append(table)
        
        self._cur.execute('INSERT INTO %s VALUES (?,?,?)' % table, (x,y,blob(PZFFormat.dumps(data.astype('float32')))))
        
    def delete_tile(self, layer, x, y):
        self._cur.execute('DELETE FROM layer%d WHERE x=? AND y=?' % layer, (x, y))
        
    def tile_exists(self, layer, x, y):
        table = 'layer%d' % layer
        if not table in self._known_tables:
            return False
        
        return self._cur.execute('SELECT 1 FROM layer%d WHERE x=? AND y=?' % layer, (x, y)).fetchone() is not None

    def get_layer_tile_coords(self, layer=0):
        coords = self._cur.execute('SELECT x, y FROM layer%d' % layer).fetchall()
        #print coords
        return coords
    
    def flush(self):
        self._conn.commit()
        
    def __del__(self):
        self._cur.close()
        self._conn.close()


TILEIO_EXT = {
    '.pzf': PZFTileIO,
    '.npy': NumpyTileIO,
    '.db': SqliteTileIO,
}

def infer_tileio_backend(base_directory):
    """ find TileIO backend for a given ImagePyramid

    Parameters
    ----------
    base_directory : str
        root directory of an ImagePyramid instance

    Returns
    -------
    class
        which TileIO derived class the ImagePyramid can be
        built with.

    Raises
    ------
    IOError
        If no file with an extension in TILEIO_EXT is found.
    """
    for root, dirs, files in os.walk(base_directory):
        for file in files:
            file_extension = os.path.splitext(file)[-1]
            if file_extension in TILEIO_EXT.keys():
                return TILEIO_EXT[file_extension]
    raise IOError("No files found for loading ImagePyramid.")

class ImagePyramid(object):
    def __init__(self, storage_directory, pyramid_tile_size=256, mdh=None, 
                 n_tiles_x = 0, n_tiles_y = 0, depth=0, x0=0, y0=0, 
                 pixel_size=1, backend=PZFTileIO):
        
        if isinstance(storage_directory, tempfile.TemporaryDirectory):
            # If the storage directory is a temporary directory, keep a reference and cleanup the directory when we delete the pyramid
            # used to support transitory pyramids. 
            self._temp_directory = storage_directory
            storage_directory = storage_directory.name
        
        self.base_dir = storage_directory
        self.tile_size = pyramid_tile_size
        
        self.pyramid_valid = False
        
        self._mdh = NestedClassMDHandler(mdh)
        self._mdh['Pyramid.TileSize'] = self.tile_size
        
        self.n_tiles_x = n_tiles_x
        self.n_tiles_y = n_tiles_y
        self.depth = depth

        self.x0 = x0
        self.y0 = y0
        self.pixel_size=pixel_size
        # TODO - should we be re-assigning these on load, not just when we create a new pyramid?
        self._mdh['Pyramid.x0'] = x0
        self._mdh['Pyramid.y0'] = y0
        self._mdh['Pyramid.PixelSize'] = pixel_size

        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir)

        #self._tilecache = TileCache()

        if backend is None:
            backend = infer_tileio_backend(self.base_dir)

        self._imgs = backend(base_dir=self.base_dir, suff='img')
        self._acc = backend(base_dir=self.base_dir, suff='acc')
        self._occ = backend(base_dir=self.base_dir, suff='occ')

    @classmethod
    def load_existing(cls, storage_directory):
        """ loads an ImagePyramid from a given directory.

        Parameters
        ----------
        storage_directory : str
            root directory of an ImagePyramid instance.

        Returns
        -------
        ImagePyramid
            based on storage_directory contents.
        """

        mdh = load_json(os.path.join(storage_directory, 'metadata.json'))

        return ImagePyramid(
            storage_directory,
            pyramid_tile_size=mdh['Pyramid.TileSize'],
            mdh=mdh,
            n_tiles_x=mdh["Pyramid.NTilesX"],
            n_tiles_y=mdh["Pyramid.NTilesY"],
            depth=mdh["Pyramid.Depth"],
            x0=mdh['Pyramid.x0'],
            y0=mdh['Pyramid.y0'],
            pixel_size=mdh["Pyramid.PixelSize"],
            backend=TILEIO_EXT.get(mdh.get("Pyramid.Backend", None),None)
        )

    def __del__(self):
        try:
            self._temp_directory.cleanup()
        except:
            pass
    
    def get_tile(self, layer, x, y):
        return self._imgs.get_tile(layer, x, y)
    
    def get_oversize_tile(self, layer, x, y, span=2):
        """
        Get an over-sized tile - allows processing on overlapping tiles

        Parameters
        ----------

        span: size of tile as a multiple of the underlying tile size

        """
        
        new_tile = np.zeros([self.tile_size * span, self.tile_size * span])
        
        for i in range(span):
            for j in range(span):
                subtile = self.get_tile(layer, x + i, y + j)
                if not subtile is None:
                    new_tile[(i * self.tile_size):((i + 1) * self.tile_size),
                    (j * self.tile_size):((j + 1) * self.tile_size)] = subtile
                    
        return new_tile
        logger.debug('Making layer %d' % (inputLevel+1))
    
    def get_layer_tile_coords(self, level):
        return self._imgs.get_layer_tile_coords(level)
    
    def _make_layer(self, inputLevel):
        from scipy import ndimage

        new_layer = inputLevel + 1
        tile_coords = self.get_layer_tile_coords(inputLevel)
        
        #print('tile_coords:', tile_coords)
        
        qsize = int(self.tile_size / 2)
        
        new_tile_coords = list(set([tuple(np.floor(np.array(tc) / 2).astype('i').tolist()) for tc in tile_coords]))
        #print('new_tile_coords:', new_tile_coords)
        
        for xc, yc in new_tile_coords:
            if not self._imgs.tile_exists(new_layer, xc, yc):
                tile = np.zeros([self.tile_size, self.tile_size])
                
                NW = self.get_tile(inputLevel, 2 * xc, 2 * yc)
                if not NW is None:
                    tile[:qsize, :qsize] = ndimage.zoom(NW, .5)
                    #print(xc, yc, 'NW')
                
                NE = self.get_tile(inputLevel, (2 * xc) + 1, (2 * yc))
                if not NE is None:
                    tile[qsize:, :qsize] = ndimage.zoom(NE, .5)
                    #print(xc, yc, 'NE')
                
                SW = self.get_tile(inputLevel, (2 * xc), (2 * yc) + 1)
                if not SW is None:
                    tile[:qsize, qsize:] = ndimage.zoom(SW, .5)
                    #print(xc, yc, 'SW')
                
                SE = self.get_tile(inputLevel, (2 * xc) + 1, (2 * yc) + 1)
                if not SE is None:
                    tile[qsize:, qsize:] = ndimage.zoom(SE, .5)
                    #print(xc, yc, 'SE')
                
                self._imgs.save_tile(new_layer, xc, yc, tile)
        
        return len(new_tile_coords)
    
    def _rebuild_base(self):
        for xc, yc in self._occ.get_layer_tile_coords(0):
            if not self._imgs.tile_exists(0, xc, yc):
                    occ = self._occ.get_tile(0, xc, yc) + 1e-9
                    sf = 1.0 / occ
                    sf[occ <= .1] = 0
                    tile_ = self._acc.get_tile(0, xc, yc) * sf

                    self._imgs.save_tile(0, xc, yc, tile_)
    
    def update_pyramid(self):
        self._rebuild_base()
        inputLevel = 0
        
        while self._make_layer(inputLevel) > 1:
            inputLevel += 1
        
        self.pyramid_valid = True
        self.depth = inputLevel
        self._imgs.flush()
    
    def _clean_tiles(self, x, y):
        level = 0
        
        while self._imgs.tile_exists(level, x, y):
            self._imgs.delete_tile(level, x, y)
            
            level += 1
            x = int(np.floor(x / 2))
            y = int(np.floor(y / 2))
            
    
    @property
    def mdh(self):
        mdh = NestedClassMDHandler(self._mdh)
        mdh['Pyramid.Depth'] = self.depth
        mdh['Pyramid.NTilesX'] = self.n_tiles_x
        mdh['Pyramid.NTilesY'] = self.n_tiles_y
        mdh['Pyramid.PixelsX'] = self.n_tiles_x * self.tile_size
        mdh['Pyramid.PixelsY'] = self.n_tiles_y * self.tile_size
        
        return mdh

    def update_base_tiles_from_frame(self, x, y, frame, weights):
        """add tile to the pyramid

        Parameters
        ----------
        x : int
            x origin of the tile (`frame`), relative to minimum x position of
            all tiles, in units of pixels
        y : int
            y origin of the tile (`frame`), relative to minimum y position of
            all tiles, in units of pixels
        frame : ndarray
            the tile frame to add
        weights : ndarray
            weights for averaging with overlapping base tiles
        """

        frameSizeX, frameSizeY = frame.shape[:2]
        
        out_folder = os.path.join(self.base_dir, '0')
        if not os.path.exists(out_folder):
            os.makedirs(out_folder)
        
        if (x < 0) or (y < 0):
            raise ValueError('base tile origin positions must be >=0')
        
        tile_xs = range(int(np.floor(x / self.tile_size)), int(np.floor((x + frameSizeX) / self.tile_size) + 1))
        tile_ys = range(int(np.floor(y / self.tile_size)), int(np.floor((y + frameSizeY) / self.tile_size) + 1))
        
        #print('tile_xs: %s, tile_ys: %s' % (tile_xs, tile_ys))

        self.n_tiles_x = max(self.n_tiles_x, max(tile_xs))
        self.n_tiles_y = max(self.n_tiles_y, max(tile_ys))
        
        for tile_x in tile_xs:
            for tile_y in tile_ys:
                self.update_base_tile(0, tile_x, tile_y, weights, frameSizeX, frameSizeY, frame)
        
        self.pyramid_valid = False

    def update_base_tile(self, layer, tile_x, tile_y, weights, frameSizeX, frameSizeY, frame):
        acc_ = self._acc.get_tile(layer, tile_x, tile_y)
        occ_ = self._occ.get_tile(layer, tile_x, tile_y)

        if (acc_ is None) or (occ_ is None):
            acc_ = np.zeros([self.tile_size, self.tile_size])
            occ_ = np.zeros([self.tile_size, self.tile_size])

        xs = max(tile_x * self.tile_size - x, 0)
        xe = min((tile_x + 1) * self.tile_size - x, frameSizeX)
        xst = max(x - tile_x * self.tile_size, 0)
        xet = min(xst + (xe - xs), self.tile_size)

        ys = max((tile_y * self.tile_size) - y, 0)
        ye = min(((tile_y + 1) * self.tile_size) - y, frameSizeY)
        yst = max(y - tile_y * self.tile_size, 0)
        yet = min(yst + (ye - ys), self.tile_size)

        acc_[xst:xet, yst:yet] += frame[xs:xe, ys:ye]
        occ_[xst:xet, yst:yet] += weights[xs:xe, ys:ye]
        self._acc.save_tile(layer, tile_x, tile_y, acc_)
        self._occ.save_tile(layer, tile_x, tile_y, occ_)

        self._clean_tiles(tile_x, tile_y)


def server_for_chunk(self, x, y, z=0, chunk_shape=[8,8,1], nr_servers=1):
    """
    Returns the server responsible for the chunk of tiles at given (x, y, z).
    """
    server_id = np.floor(x/chunk_shape[0])
    server_id = server_id + np.floor(y/chunk_shape[1]) * nr_servers * .3
    server_id = server_id + np.floor(z/chunk_shape[2]) % nr_servers
    return server_id


class PartialPyramid:
    """
    Subclass of ImagePyramid which supports distribution of pyramid files over a PYME cluster.
    Implementation from the microscope side.
    """
    def __init__(
        self, storage_directory, pyramid_tile_size=256, mdh=None, 
        n_tiles_x=0, n_tiles_y=0, depth=0, x0=0, y0=0, 
        pixel_size=1, backend=PZFTileIO, chunk_shape=[8,8,1], nr_servers=1, 
        server_idx=0,
    ):
        super().__init__(
            storage_directory, pyramid_tile_size=pyramid_tile_size, mdh=mdh,
            n_tiles_x=n_tiles_, n_tiles_y=n_tiles_y, depth=depth, x0=x0, y0=y0, 
            pixel_size=pixel_size, backend=backend
        )
        self.chunk_shape = chunk_shape
        self.server_idx = server_idx

    def update_base_tile_from_request_data(self, data):
        data_dict = json.load(data.decode())
        frame_slice = numpy.asarray(data_dict["frame_data"])
        frame_slice = frame_slice.reshape(data_dict["frame_shape"])
        weights_slice = numpy.asarray(data_dict["weights_data"])
        weights_slice = weights_slice.reshape(data_dict["weights_shape"])
        tile_x = data_dict["coords"][0]
        tile_y = data_dict["coords"][1]
        self.update_base_tile_from_slices(0, tile_x, tile_y, frame_slice, weights_slice)

    def update_base_tile_from_slices(self, layer, tile_x, tile_y, frame_slice, weights_slice):
        acc_ = self._acc.get_tile(layer, tile_x, tile_y)
        occ_ = self._occ.get_tile(layer, tile_x, tile_y)

        if (acc_ is None) or (occ_ is None):
            acc_ = np.zeros([self.tile_size, self.tile_size])
            occ_ = np.zeros([self.tile_size, self.tile_size])

        xs = max(tile_x * self.tile_size - x, 0)
        xe = min((tile_x + 1) * self.tile_size - x, frameSizeX)
        xst = max(x - tile_x * self.tile_size, 0)
        xet = min(xst + (xe - xs), self.tile_size)

        ys = max((tile_y * self.tile_size) - y, 0)
        ye = min(((tile_y + 1) * self.tile_size) - y, frameSizeY)
        yst = max(y - tile_y * self.tile_size, 0)
        yet = min(yst + (ye - ys), self.tile_size)


        acc_[xst:xet, yst:yet] += frame_slice
        occ_[xst:xet, yst:yet] += weights_slice
        self._acc.save_tile(layer, tile_x, tile_y, acc_)
        self._occ.save_tile(layer, tile_x, tile_y, occ_)

        self._clean_tiles(tile_x, tile_y)



class DistributedImagePyramid(ImagePyramid):
    from PYME.cluster import HTTPDataServer
    from PYME.IO import clusterIO
    import socket
    import requests
    import logging
    import json

    """
    Subclass of ImagePyramid which supports distribution of pyramid files over a PYME cluster.
    Implementation from the microscope side.
    """
    def __init__(
        self, storage_directory, pyramid_tile_size=256, mdh=None, 
        n_tiles_x=0, n_tiles_y=0, depth=0, x0=0, y0=0, 
        pixel_size=1, backend=PZFTileIO, n_servers=1, chunk_shape=[8,8,1], timeout=10, repeats=3
    ):
        super().__init__(
            storage_directory, pyramid_tile_size=pyramid_tile_size, mdh=mdh,
            n_tiles_x=n_tiles_, n_tiles_y=n_tiles_y, depth=depth, x0=x0, y0=y0, 
            pixel_size=pixel_size, backend=backend
        )
        self.chunk_shape = chunk_shape
        self.timeout = timeout
        self.repeats = repeats
        self.servers = [((k), (v)) for k, v in clusterIO.get_ns().get_advertised_services()]
        self.sessions = [requests.Session() for _, _ in self.servers]
        self.logger = logging.getLogger(__name__)

    def update_base_tiles_from_frame(self, x, y, frame, weights):
        frameSizeX, frameSizeY = frame.shape[:2]
        
        out_folder = os.path.join(self.base_dir, '0')
        if not os.path.exists(out_folder):
            os.makedirs(out_folder)
        
        if (x < 0) or (y < 0):
            raise ValueError('base tile origin positions must be >=0')
        
        tile_xs = range(int(np.floor(x / self.tile_size)), int(np.floor((x + frameSizeX) / self.tile_size) + 1))
        tile_ys = range(int(np.floor(y / self.tile_size)), int(np.floor((y + frameSizeY) / self.tile_size) + 1))

        self.n_tiles_x = max(self.n_tiles_x, max(tile_xs))
        self.n_tiles_y = max(self.n_tiles_y, max(tile_ys))

        for tile_x in tile_xs:
            for tile_y in tile_ys:
                server_idx = server_for_chunk(
                    tile_x, tile_y, layer, chunk_shape=self.chunk_shape, nr_servers=len(self.sessions)
                )
                frame_slice, weights_slice = get_tile_slices_from(
                    tile_x, tile_y, frameSizeX, frameSizeY, frame, weights
                )
                self.send_data_to_server(tile_x, tile_y, frame_slice, weights_slice, server_idx)

        self.pyramid_valid = False

    def get_tile_slices_from(self, tile_x, tile_y, frameSizeX, frameSizeY, frame, weights):
        xs = max(tile_x * self.tile_size - x, 0)
        xe = min((tile_x + 1) * self.tile_size - x, frameSizeX)
        xst = max(x - tile_x * self.tile_size, 0)
        xet = min(xst + (xe - xs), self.tile_size)

        ys = max((tile_y * self.tile_size) - y, 0)
        ye = min(((tile_y + 1) * self.tile_size) - y, frameSizeY)
        yst = max(y - tile_y * self.tile_size, 0)
        yet = min(yst + (ye - ys), self.tile_size)

        frame_slice = frame[xs:xe, ys:ye]
        weights_slice = weights[xs:xe, ys:ye]
        return frame_slice, weights_slice

    def send_data_to_server(self, x, y, frame, weights, server_idx):
        name, info = self.servers[server_idx]
        filename = self.storage_directory
        url = 'http://%s:%d/%s' % (socket.inet_ntoa(info.address), info.port, filename)
        url = url.encode()
        data_dict = {
            "frame_shape": frame.shape,
            "frame_data": frame.ravel().tolist(),
            "weights_shape": weights.shape,
            "weights_data": weights.ravel().tolist(),
            "coords": [x, y],
        }
        data = json.dumps(data_dict).encode()
        for repeat in range(self.repeats):
            session = self.sessions[server_idx]
            try:
                response = session.put(url, data, self.timeout)
                if not response.status_code == 200:
                    raise RuntimeError('Put failed with %d: %s' % (return_code.status_code, return_code.content))
            except response.ConnectTimeout:
                if repeat + 1 == self.repeats:
                    self.logger.error('Timeout attempting to put file: %s, after 3 retries, aborting' % url)
                    raise
                else:
                    self.logger.warn('Timeout attempting to put file: %s, retrying' % url)
            finally:
                try:
                    response.close()
                except:
                    pass

def get_position_from_events(events, mdh):
    """Use acquisition events to create a mapping between frame number and
    stage position
    """
    from PYME.Analysis import piecewiseMapping
    x0 = mdh.getOrDefault('Positioning.x', 0)
    y0 = mdh.getOrDefault('Positioning.y', 0)
    
    xm = piecewiseMapping.GeneratePMFromEventList(events, mdh, mdh['StartTime'], x0, b'ScannerXPos', 0)
    ym = piecewiseMapping.GeneratePMFromEventList(events, mdh, mdh['StartTime'], y0, b'ScannerYPos', 0)
    
    return xm, ym


def tile_pyramid(out_folder, ds, xm, ym, mdh, split=False, skipMoveFrames=False, shiftfield=None,
                 mixmatrix=[[1., 0.], [0., 1.]],
                 correlate=False, dark=None, flat=None, pyramid_tile_size=256):
    """Create a tile pyramid from which an ImagePyramid can be created

    Parameters
    ----------
    out_folder : str
        directory to save pyramid tiles(/directories)
    ds : PYME.IO.DataSources.BaseDataSource, np.ndarray
        array-like image
    xm : np.ndarray or PYME.Analysis.piecewiseMapping.piecewiseMap
        x positions of frames in ds. Raw stage positions in [um]. ImagePyramid
        origin will be at at minimum x, and offset to camera chip origin will
        be handled in SupertileDatasource tile_coords_um method.
        to the camera chip origin.
    ym : np.ndarray or PYME.Analysis.piecewiseMapping.piecewiseMap
        y positions of frames in ds. Raw stage positions in [um]. ImagePyramid
        origin will be at at minimum y, and offset to camera chip origin will
        be handled in SupertileDatasource tile_coords_um method.
    mdh : PYME.IO.MetaDataHandler.MDataHandlerBase
        metadata for ds
    split : bool, optional
        whether this is a splitter datasource and should be treated like one,
        by default False
    skipMoveFrames : bool, optional
        flag to drop frames which are the first frame acquired at a given
        position, by default False
    shiftfield : [type], optional
        required for splitter data, see PYME.Acquire.Hardware.splitter, by 
        default None
    mixmatrix : list, optional
        for splitter data, see PYME.Acquire.Hardware.splitter, by 
        default [[1., 0.], [0., 1.]]
    correlate : bool, optional
        whether to add a 300 pixel padding to the edges, by default False
    dark : ndarray, float, optional
        (appropriately-cropped or scalar) dark frame (analog-digital offset)
        calibration to subtract when adding frames to the pyramid, by default
        None, in which case Camera.ADOffset from metadata will be used, if 
        available
    flat : ndarray, optional
        (appropriately-cropped or scalar) flatfield calibration to apply to 
        frames when adding them to the pyramid, by default None
    pyramid_tile_size : int, optional
        base tile size, by default 256 pixels

    Returns
    -------
    ImagePyramid
        coalesced/averaged/etc multilevel ImagePyramid instance
    
    Notes
    -----
    Code is currently somewhat alpha in that the splitter functionality is 
    more or less untested, and we only get tile orientations right for primary
    cameras (i.e. when the stage is registered with multipliers to match the
    camera, rather than camera registered with orientation metadata to match it
    to the stage)

    """
    frameSizeX, frameSizeY, numFrames = ds.shape[:3]
    
    if split:
        from PYME.Acquire.Hardware import splitter
        frameSizeY /= 2
        nchans = 2
        unmux = splitter.Unmixer(shiftfield, mdh.voxelsize_nm.x)
    else:
        nchans = 1
    
    #x & y positions of each frame
    xps = xm(np.arange(numFrames)) if not isinstance(xm, np.ndarray) else xm
    yps = ym(np.arange(numFrames)) if not isinstance(ym, np.ndarray) else ym

    #give some room at the edges
    bufSize = 0
    if correlate:
        bufSize = 300
    
    # to avoid building extra, empty tiles, the pyramid origin is the minimum
    # x and y position present in the tiles
    x0_pyramid, y0_pyramid = xps.min(), yps.min()
    xps -= x0_pyramid
    yps -= y0_pyramid

    # calculate origin independent of the camera ROI setting to store in
    # metadata for use in e.g. SupertileDatasource.DataSource.tile_coords_um
    x0_cam, y0_cam = get_camera_physical_roi_origin(mdh)
    x0 = x0_pyramid + mdh.voxelsize_nm.x / 1e3 * x0_cam
    y0 = y0_pyramid + mdh.voxelsize_nm.y / 1e3 * y0_cam

    #convert to pixels
    xdp = (bufSize + (xps / (mdh.getEntry('voxelsize.x'))).round()).astype('i')
    ydp = (bufSize + (yps / (mdh.getEntry('voxelsize.y'))).round()).astype('i')
    
    #calculate a weighting matrix (to allow feathering at the edges - TODO)
    weights = np.ones((frameSizeX, frameSizeY, nchans))
    #weights[:, :10, :] = 0 #avoid splitter edge artefacts
    #weights[:, -10:, :] = 0
    
    #print weights[:20, :].shape
    edgeRamp = min(100, int(.25 * ds.shape[0]))
    weights[:edgeRamp, :, :] *= np.linspace(0, 1, edgeRamp)[:, None, None]
    weights[-edgeRamp:, :, :] *= np.linspace(1, 0, edgeRamp)[:, None, None]
    weights[:, :edgeRamp, :] *= np.linspace(0, 1, edgeRamp)[None, :, None]
    weights[:, -edgeRamp:, :] *= np.linspace(1, 0, edgeRamp)[None, :, None]
    
    # get splitter ROI coordinates in units of pixels
    ROIX1 = x0_cam + 1  # TODO - is splitter 1-indexed?
    ROIY1 = y0_cam + 1
    ROIX2 = ROIX1 + mdh.getEntry('Camera.ROIWidth')
    ROIY2 = ROIY1 + mdh.getEntry('Camera.ROIHeight')
    
    if dark is None:
        dark = float(mdh.getOrDefault('Camera.ADOffset', 0))

    P = ImagePyramid(out_folder, pyramid_tile_size, x0=x0, y0=y0, 
                     pixel_size=mdh.getEntry('voxelsize.x'))

    logger.debug('Adding base tiles ...')
    
    t1 = time.time()
    for i in range(int(mdh.getEntry('Protocol.DataStartsAt')), numFrames):
        if xdp[i - 1] == xdp[i] or not skipMoveFrames:
            x_i = xdp[i]
            y_i = ydp[i]
            d = ds[:, :, i].astype('f') - dark
            if not flat is None:
                d = d * flat
            
            if split:
                d = np.concatenate(unmux.Unmix(d, mixmatrix, dark, [ROIX1, ROIY1, ROIX2, ROIY2]), 2)

            d_weighted = weights * d
            # TODO - account for orientation so this works for non-primary cams
            P.update_base_tiles_from_frame(x_i, y_i, d_weighted.squeeze(), weights.squeeze())
                
    
    t2 = time.time()
    logger.debug('Added base tiles in %fs' % (t2 - t1))
    #P._occ.flush()
    logger.debug(time.time() - t2)
    logger.debug('Updating pyramid ...')
    P.update_pyramid()
    logger.debug(time.time() - t2)
    logger.debug('Done')

    with open(os.path.join(P.base_dir, 'metadata.json'), 'w') as f:
        f.write(P.mdh.to_JSON())
    
    return P

def create_pyramid_from_dataset(filename, outdir, tile_size=128, **kwargs):
    from PYME.IO import image
    dataset = image.ImageStack(filename=filename)
    
    xm, ym = get_position_from_events(dataset.events, dataset.mdh)
    
    #print(xm(np.arange(dataset.data.shape[2])))
    #print(ym(np.arange(dataset.data.shape[2])))
    
    p = tile_pyramid(outdir, dataset.data, xm, ym, dataset.mdh, pyramid_tile_size=tile_size)
    
    return p
        
        
        
if __name__ == '__main__':
    import sys
    from PYME.util import mProfile
    #mProfile.profileOn(['tile_pyramid.py',])
    input_stack, output_dir = sys.argv[1:]
    import time
    t1 = time.time()
    create_pyramid_from_dataset(input_stack, output_dir)
    logger.debug(time.time() - t1)
    #mProfile.report()
    
    
    
    
