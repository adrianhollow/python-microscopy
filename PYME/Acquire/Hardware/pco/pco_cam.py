# -*- coding: utf-8 -*-

"""
Created on Mon August 10 2020

@author: zacsimile
"""

from PYME.Acquire.Hardware.Camera import Camera
from PYME.Acquire import eventLog

import numpy as np
import logging
logger = logging.getLogger(__name__)

# We use https://pypi.org/project/pco/
# For API docs, see https://www.pco.de/fileadmin/user_upload/pco-manuals/pco.sdk_manual.pdf and
# https://www.pco.de/fileadmin/user_upload/pco-manuals/pco.recorder_manual.pdf
try:
    import pco
except ModuleNotFoundError:
    raise ModuleNotFoundError('Please install the pco package from https://pypi.org/project/pco/.')

timebase = {'ns': 1e-9, 'us': 1e-6, 'ms': 1e-3}  # Conversions from a pco dictionary

class PcoCam(Camera):
    def __init__(self, camNum):
        Camera.__init__(self)
        self.camNum = camNum
        self.initalized = False

    def Init(self):
        self.cam = pco.Camera(debuglevel='error')
        self.SetDescription()
        self._mode = self.MODE_CONTINUOUS
        self.SetHotPixelCorrectionMode('off')
        self.buffer_size = 0
        self.curr_frame = 0
        self.initalized = True

    @property
    def noise_properties(self):
        return self.noiseProps

    def SetDescription(self):
        self.desc = self.cam.sdk.get_camera_description()

    def ExpReady(self):
        return self.GetNumImsBuffered() >= 1

    def GetName(self):
        return self.cam.sdk.get_camera_name()['camera name']

    def CamReady(self):
        # return self.cam.sdk.get_camera_busy_status()['busy status'] == 0
        return self.initalized

    def ExtractColor(self, chSlice, mode):

        if self.curr_frame > self.GetNumImsBuffered():
            # Take a beat, we're going too fast
            return True

        if self.curr_frame >= self.buffer_size:
            # We're using a ring buffer, so wrap
            self.curr_frame = 0

        # Grab the image (unused metadata _)
        chSlice, _ = self.cam.image(self.curr_frame)

        # Increment the frame number
        self.curr_frame += 1

        return 0

    def GetHeadModel(self):
        return self.cam.sdk.get_camera_type()['camera type']

    def GetSerialNumber(self):
        return str(self.cam.sdk.get_camera_type()['serial number'])

    def SetIntegTime(self, time):
        lb = self.desc['Min Expos DESC']*1e-9  # ns
        ub = self.desc['Max Expos DESC']*1e-3  # ms
        step = self.desc['Min Expos Step DESC']*1e-9  # ns
        
        # Round to a multiple of time step
        time = np.floor(time/step)*step

        # Don't let this go out of bounds
        time = np.clip(time, lb, ub)
            
        self.cam.set_exposure_time(time)

        self.SetDescription() # Update the description, for safety. TODO: Is this necessary??

    def GetIntegTime(self):
        d = self.cam.sdk.get_delay_exposure_time()
        return d['exposure']*timebase[d['exposure timebase']] 

    def GetCycleTime(self):
        d = self.cam.sdk.get_delay_exposure_time()
        return d['exposure']*timebase[d['exposure timebase']] + d['delay']*timebase[d['delay timebase']]

    def GetCCDWidth(self):
        return self.desc['max. horizontal resolution standard']

    def GetCCDHeight(self):
        return self.desc['max. vertical resolution standard']

    def GetPicWidth(self):
        return self.GetROI()[2] - self.GetROI()[0]

    def GetPicHeight(self):
        return self.GetROI()[3] - self.GetROI()[1]

    def SetHorizontalBin(self, value):
        b_max, step_type = self.desc['max. binning horizontal'], self.desc['binning horizontal stepping']

        if step_type == 0:
            # binary binning, round to powers of two
            value = 1<<(value-1).bit_length()
        else:
            value = np.round(value) # integer

        value = np.clip(value, 1, b_max)

        self.cam.sdk.set_binning(value, self.GetVerticalBin())

        self.SetDescription() # Update the description, for safety. TODO: Is this necessary??

    def GetHorizontalBin(self):
        return self.cam.sdk.get_binning()['binning x']

    def SetVerticalBin(self, value):
        b_max, step_type = self.desc['max. binning vert'], self.desc['binning vert stepping']

        if step_type == 0:
            # binary binning, round to powers of two
            value = 1<<(value-1).bit_length()
        else:
            value = np.round(value) # integer

        value = np.clip(value, 1, b_max)

        self.cam.sdk.set_binning(self.GetHorizontalBin(), value)

        self.SetDescription() # Update the description, for safety. TODO: Is this necessary??

    def GetVerticalBin(self):
        return self.cam.sdk.get_binning()['binning y']

    def GetSupportedBinning(self):
        import itertools

        bx_max, bx_step_type = self.desc['max. binning horizontal'], self.desc['binning horizontal stepping']
        by_max, by_step_type = self.desc['max. binning vert'], self.desc['binning vert stepping']

        if bx_step_type == 0:
            # binary
            x = [2**j for j in np.arange((bx_max).bit_length())]
        else:
            x = [j for j in np.arange(bx_max)]

        if by_step_type == 0:
            y = [2**j for j in np.arange((by_max).bit_length())]
        else:
            y = [j for j in np.arange(by_max)]

        return list(itertools.product(x,y))

    def SetROI(self, x0, y0, x1, y1):
        dx = self.desc['roi hor steps']
        dy = self.desc['roi vert steps']

        # Round to a multiple of dx, dy
        x0 = np.floor(x0/dx)*dx
        y0 = np.floor(y0/dy)*dy
        x1 = np.floor(x1/dx)*dx
        y1 = np.floor(y1/dy)*dy

        lx_max = self.desc['max. horizontal resolution standard']
        ly_max = self.desc['max. vertical resolution standard']
        lx_min = self.desc['min size horz']
        ly_min = self.desc['min size vert']

        # Don't let the ROI go out of bounds
        x0 = np.clip(x0, 0, lx_max)
        y0 = np.clip(y0, 0, ly_max)
        x1 = np.clip(x1, 0, lx_max)
        y1 = np.clip(y1, 0, ly_max)

        # Make sure everything is ordered
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0

        # Don't let us choose too small an ROI
        if (x1-x0) < lx_min:
            logger.debug('Selected ROI width is too small, automatically adjusting to {}.'.format(lx_min))
            guess_pos = x0+lx_min
            # Deal with boundaries
            if guess_pos <= lx_max:
                x1 = guess_pos
            else:
                x0 = x1-lx_min
        if (y1-y0) < ly_min:
            logger.debug('Selected ROI height is too small, automatically adjusting to {}.'.format(ly_min))
            guess_pos = y0+ly_min
            if guess_pos <= ly_max:
                y1 = guess_pos
            else:
                y0 = y1-ly_min

        self.cam.sdk.set_roi(x0, y0, x1, y1)

        logger.debug('ROI set: x0 %3.1f, y0 %3.1f, w %3.1f, h %3.1f' % (x0, y0, x1-x0, y1-y0))

        self.SetDescription() # Update the description, for safety. TODO: Is this necessary??

    def GetROI(self):
        roi = self.cam.sdk.get_roi()
        return roi['x0'], roi['y0'], roi['x1'], roi['y1']
    
    def GetElectrTemp(self):
        return self.cam.sdk.get_temperature()['camera temperature']  # FIXME: should this be 'power temperature'?

    def GetCCDTemp(self):
        return self.cam.sdk.get_temperature()['sensor temperature']

    def GetCCDTempSetPoint(self):
        return self.cam.sdk.get_cooling_setpoint_temperature()['cooling setpoint temperature']

    def SetCCDTemp(self, temp):
        lb = self.desc['Min Cool Set DESC']
        ub = self.desc['Max Cool Set DESC']
        temp = np.clip(temp, lb, ub)

        self.cam.sdk.set_cooling_setpoint_temperature(temp)

    def GetAcqusitionMode(self):
        return self._mode

    def SetAcqusitionMode(self, mode):
        if mode in [self.MODE_CONTINUOUS, self.MODE_SINGLE_SHOT]:
            self._mode = mode
        else:
            raise RuntimeError('Mode %d not supported' % mode)

    def StartExposure(self):
        if self._mode == self.MODE_SINGLE_SHOT:
            self.cam.record(number_of_images=1, mode='sequence')
        elif self._mode == self.MODE_CONTINUOUS:
            # Allocate buffer (2 seconds of buffer)
            self.buffer_size = int(max(int(2.0*self.GetFPS()), 1))
            self.cam.record(number_of_images=self.buffer_size, mode='ring buffer')

        eventLog.logEvent('StartAq', '')

    def StopAq(self):
        self.cam.stop()

    def GetNumImsBuffered(self):
        return self.cam.rec.get_status()['dwProcImgCount']

    def GetBufferSize(self):
        return self.cam.rec.get_settings()['maximum number of images']

    def GetFPS(self):
        return 1.0/self.GetCycleTime()

    def SetHotPixelCorrectionMode(self, mode):
        if mode in ['on', 'off']:
            self.cam.sdk.set_hot_pixel_correction_mode(mode)
        else:
            logger.debug('Hot Pixel Correction not set. Invalid mode {}. Choose "on" or "off".'.format(mode))

    def Shutdown(self):
        self.cam.close()
