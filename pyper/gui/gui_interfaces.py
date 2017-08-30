# -*- coding: utf-8 -*-
"""
*************************
The gui_interfaces module
*************************

Creates the class links to allow the graphical interface.
It essentially implements a class for each graphical interface tab.

:author: crousse
"""

import sys, os
import csv
import re
import imp

import cv2

import numpy as np
from scipy.misc import imsave
import matplotlib
matplotlib.use('qt5agg')  # For OSX otherwise, the default backend doesn't allow to draw to buffer
from matplotlib import pyplot as plt

from PyQt5.QtWidgets import QFileDialog
from PyQt5.QtCore import QObject, pyqtSlot, QVariant, QTimer
from PyQt5.QtCore import Qt

from pyper.tracking.tracking import GuiTracker
from pyper.tracking.tracker_plugins import PupilGuiTracker
from pyper.video.video_stream import QuickRecordedVideoStream as VStream
from pyper.video.video_stream import ImageListVideoStream
from pyper.contours.roi import Circle, Rectangle, Ellipse, FreehandRoi
from pyper.analysis import video_analysis
from pyper.camera.camera_calibration import CameraCalibration
from pyper.gui.image_providers import CvImageProvider

from pyper.exceptions.exceptions import VideoStreamIOException

VIDEO_FILTERS = "Videos (*.avi *.h264 *.mpg)"
VIDEO_FORMATS = ('.avi', '.h264', '.mpg')

Tracker = GuiTracker
TRACKER_CLASSES = {
    'GuiTracker': GuiTracker,
    'PupilGuiTracker': PupilGuiTracker
}

class BaseInterface(QObject):
    """
    Abstract interface
    This class is meant to be sub-classed by the other classes of the module
    PlayerInterface, TrackerIface (base themselves to ViewerIface, CalibrationIface, RecorderIface)
    It supplies the base methods and attributes to register an object with video in qml.
    It also possesses an instance of ParamsIface to 
    """
    def __init__(self, app, context, parent, params, display_name, provider_name, timer_speed=20):
        QObject.__init__(self, parent)
        self.app = app  # necessary to avoid QPixmap bug: must construct a QGuiApplication before
        self.ctx = context
        self.win = parent
        self.display_name = display_name
        self.provider_name = provider_name
        self.params = params
        
        self.timer = QTimer(self)
        self.timer_speed = timer_speed
        self.timer.timeout.connect(self.get_img)

    def get_img(self):
        """The method called by self.timer to play the video"""
        if -1 <= self.stream.current_frame_idx < self.n_frames:
            try:
                self.display.reload()
                self._update_display_idx()
            except EOFError:
                self.timer.stop()
        else:
            self.timer.stop()

    def _set_display(self):
        """
        Gets the display from the qml code
        
        :param string display_name: The exact name of the display in the qml code
        """
        self.display = self.win.findChild(QObject, self.display_name)

    def _update_display_idx(self):
        """
        Updates the value of the display progress bar
        """
        self.display.setProperty('value', self.stream.current_frame_idx)
        
    def _set_display_max(self):
        """
        Sets the maximum of the display progress bar
        """
        self.display.setProperty('maximumValue', self.n_frames)
    
    @pyqtSlot(result=QVariant)
    def get_frame_idx(self):
        """
        pyQT slot to return the index of the currently displayed frame
        """
        return str(self.stream.current_frame_idx)
        
    @pyqtSlot(result=QVariant)
    def get_n_frames(self):
        """
        pyQT slot to return the number of frames of the current display
        """
        return self.n_frames
        
    def _update_img_provider(self):
        """
        Registers the objects image provider with the qml code
        Based on self.provider_name
        """
        engine = self.ctx.engine()
        self.image_provider = CvImageProvider(requestedImType='pixmap', stream=self.stream)
        engine.addImageProvider(self.provider_name, self.image_provider)


class PlayerInterface(BaseInterface):
    """
    This (abstract) class extends the BaseInterface to allow controlable videos (play, pause, forward...)
    """
    @pyqtSlot()
    def play(self):
        """
        Start video (timer) playback
        """
        self.timer.start(self.timer_speed)

    @pyqtSlot()
    def pause(self):
        """
        Pause video (timer) playback
        """
        self.timer.stop()

    @pyqtSlot(QVariant)
    def move(self, step_size):
        """
        Moves in the video by stepSize
        
        :param int step_size: The number of frames to scroll by (positive or negative)
        """
        target_frame = self.stream.current_frame_idx
        target_frame -= 1  # reset
        target_frame += int(step_size)
        self.stream.current_frame_idx = self._validate_frame_idx(target_frame)
        self.get_img()

    @pyqtSlot(QVariant)
    def seek_to(self, frame_idx):
        """
        Seeks directly to frameIdx in the video
        
        :param int frame_idx: The frame to get to
        """
        self.stream.current_frame_idx = self._validate_frame_idx(frame_idx)
        self.get_img()
    
    def _validate_frame_idx(self, frame_idx):
        """
        Checks if the supplied frameIdx is within [0:n_frames]

        :returns: A bound index
        :rtype: int
        """
        if frame_idx >= self.n_frames:
            frame_idx = self.n_frames - 1
        elif frame_idx < 0:
            frame_idx = 0
        return frame_idx


class ViewerIface(PlayerInterface):
    """
    Implements the PlayerInterface class with a QuickRecordedVideoStream
    It is meant for video preview with frame precision seek
    """

    @pyqtSlot()
    def load(self):
        """
        Loads the video into memory
        """
        try:
            self.stream = VStream(self.params.src_path, 0, 1)
        except VideoStreamIOException:
            self.stream = None
            error_screen = self.win.findChild(QObject, 'viewerVideoLoadingErrorScreen')
            error_screen.setProperty('doFlash', True)
            return
        self.n_frames = self.stream.n_frames - 1

        self._set_display()
        self._set_display_max()
        self._update_img_provider()


class CalibrationIface(PlayerInterface):
    """
    Implements the PlayerInterface class with an ImageListVideoStream
    It uses the CameraCalibration class to compute the camera matrix from a set of images containing a
    chessboard pattern.
    """
    def __init__(self, app, context, parent, params, display_name, provider_name, timer_speed=200):
        PlayerInterface.__init__(self, app, context, parent, params, display_name, provider_name, timer_speed)
        
        self.n_columns = 9
        self.n_rows = 6
        self.matrix_type = 'normal'
        
        self._set_display()
        self._set_display()

    @pyqtSlot()
    def calibrate(self):
        """
        Compute the camera matrix 
        """
        self.calib = CameraCalibration(self.n_columns, self.n_rows)
        self.calib.calibrate(self.src_folder)
        self.params.calib = self.calib
        
        self.n_frames = len(self.calib.src_imgs)
        
        self.stream = ImageListVideoStream(self.calib.src_imgs)
        self._set_display()
        self._set_display_max()
        
        self._update_img_provider()

    @pyqtSlot(result=QVariant)
    def get_n_rows(self):
        """
        Get the number of inner rows in the chessboard pattern
        """
        return self.n_rows

    @pyqtSlot(QVariant)
    def set_n_rows(self, n_rows):
        """Set the number of inner rows in the chessboard pattern"""
        self.n_rows = int(n_rows)

    @pyqtSlot(result=QVariant)
    def get_n_columns(self):
        """Get the number of inner columns in the chessboard pattern"""
        return self.n_columns

    @pyqtSlot(QVariant)
    def set_n_columns(self, n_columns):
        """Set the number of inner rows in the chessboard pattern"""
        self.n_columns = int(n_columns)

    @pyqtSlot(result=QVariant)
    def get_folder_path(self):
        """Get the path to the folder where the images with the pattern are stored"""
        diag = QFileDialog()
        src_folder = diag.getExistingDirectory(parent=diag, caption="Chose directory", directory=os.getenv('HOME'))
        self.src_folder = src_folder
        return src_folder

    @pyqtSlot(QVariant)
    def set_matrix_type(self, matrix_type):
        """
        Set the matrix type to be saved. Resolution independant (normal) or dependant (optimized)
        
        :param string matrix_type: The type of matrix to be saved. One of ['normal', 'optimized']
        """
        matrix_type = matrix_type.lower()
        if matrix_type not in ['normal', 'optimized']:
            raise KeyError("Expected one of ['normal', 'optimized'], got {}".format(matrix_type))
        else:
            self.matrix_type = matrix_type

    @pyqtSlot()
    def save_camera_matrix(self):
        """
        Save the camera matrix selected as self.matrixType
        """
        diag = QFileDialog()
        dest_path = diag.getSaveFileName(parent=diag,
                                         caption='Save matrix',
                                         directory=os.getenv('HOME'),
                                         filter='Numpy (.npy)')
        dest_path = dest_path[0]
        if dest_path:
            if self.matrix_type == 'normal':
                np.save(dest_path, self.camera_matrix)
            elif self.matrix_type == 'optimized':
                np.save(dest_path, self.optimal_camera_matrix)

    @pyqtSlot(QVariant)
    def set_frame_type(self, frame_type):
        """
        Selects the type of frame to be displayed. (Before, during or after distortion correction)
        
        :param string frame_type: The selected frame type. One of ['source', 'detected', 'corrected']
        """
        frame_type = frame_type.lower()
        current_index = self.stream.current_frame_idx
        if frame_type == "source":
            imgs = self.calib.src_imgs
        elif frame_type == "detected":
            imgs = self.calib.detected_imgs
        elif frame_type == "corrected":
            imgs = self.calib.corrected_imgs
        else:
            raise KeyError("Expected one of ['source', 'detected', 'corrected'], got {}".format(frame_type))
        self.stream = ImageListVideoStream(imgs)
        self._update_img_provider()
        self.stream.current_frame_idx = self._validate_frame_idx(current_index - 1)  # reset to previous position
        self.get_img()


class TrackerIface(BaseInterface):
    """
    This class implements the BaseInterface to provide a qml interface
    to the GuiTracker (or subclass thereof) object of the tracking module.
    """
    def __init__(self, app, context, parent, params, display_name, provider_name, analysis_provider_1, analysis_provider_2):
        BaseInterface.__init__(self, app, context, parent, params, display_name, provider_name)
        
        self.positions = []
        self.roi = None
        self.tracking_region_roi = None
        self.analysis_image_provider = analysis_provider_1
        self.analysisImageProvider2 = analysis_provider_2

    @pyqtSlot(QVariant, result=QVariant)
    def get_row(self, idx):
        """
        Get the data (position and distancesFromArena) at row idx
        
        :param int idx: The index of the row to return
        """
        idx = int(idx)
        if 0 <= idx < len(self.positions):
            row = [idx] + list(self.positions[idx]) + list(self.distances_from_arena[idx])
            return [str(e) for e in row]
        else:
            return -1

    @pyqtSlot()
    def load(self):
        """
        Load the video and create the GuiTracker object (or subclass)
        Also registers the analysis image providers (for the analysis tab) with QT
        """
        try:
            self.tracker = Tracker(self, src_file_path=self.params.src_path, dest_file_path=None,
                                   n_background_frames=1, plot=True,
                                   fast=True, camera_calibration=self.params.calib,
                                   callback=None)
        except VideoStreamIOException:
            self.tracker = None
            error_screen = self.win.findChild(QObject, 'videoLoadingErrorScreen')
            error_screen.setProperty('doFlash', True)
            return
        self.stream = self.tracker  # To comply with BaseInterface
        self.tracker.roi = self.roi

        self.n_frames = self.tracker._stream.n_frames - 1
        self.current_frame_idx = self.tracker._stream.current_frame_idx
        
        if self.params.end_frame_idx == -1:
            self.params.end_frame_idx = self.n_frames
        
        self._set_display()
        self._set_display_max()
        self._update_img_provider()

    @pyqtSlot()
    def start(self):
        """
        Start the tracking of the loaded video with the parameters from self.params
        """
        if self.tracker is None:
            return

        self.positions = []  # reset between runs
        self.distances_from_arena = []
        
        self.tracker._stream.bg_start_frame = self.params.bg_frame_idx  # FIXME: params attributes case
        n_background_frames = self.params.n_bg_frames
        self.tracker._stream.bg_end_frame = self.params.bg_frame_idx + n_background_frames - 1
        self.tracker.track_from = self.params.start_frame_idx
        self.tracker.track_to = self.params.end_frame_idx if (self.params.end_frame_idx > 0) else None
        
        self.tracker.threshold = self.params.detection_threshold
        self.tracker.min_area = self.params.objects_min_area
        self.tracker.max_area = self.params.objects_max_area
        self.tracker.teleportation_threshold = self.params.teleportation_threshold
        
        self.tracker.n_sds = self.params.n_sds
        self.tracker.clear_borders = self.params.clear_borders
        self.tracker.normalise = self.params.normalise
        self.tracker.extract_arena = self.params.extract_arena
        
        self.tracker.set_roi(self.roi)
        self.tracker.set_tracking_region_roi(self.tracking_region_roi)
            
        self.timer.start(self.timer_speed)

    @pyqtSlot()
    def stop(self):
        """
        The qt slot to self._stop()
        """
        self._stop('Recording stopped manually')
        
    def _stop(self, msg):
        """
        Stops the tracking gracefully
        
        :param string msg: The message to print upon stoping
        """
        self.timer.stop()
        self.tracker._stream.stop_recording(msg)

    def __get_scaling_factors(self, width, height):
        stream_width, stream_height = self.tracker._stream.size  # flipped for openCV
        horizontal_scaling_factor = stream_width / width
        vertical_scaling_factor = stream_height / height
        return horizontal_scaling_factor, vertical_scaling_factor

    # @pyqtSlot(QVariant, QVariant, QVariant, QVariant, QVariant, QVariant)  # CIRCLE ROI
    # def set_roi(self, width, height, x, y, roi_width, roi_height):
    #     """
    #     Sets the ROI (in which to check for the specimen) from the one drawn in QT
    #     Scaling is applied to match the (resolution difference) between the representation
    #     of the frames in the GUI (on which the user draws the ROI) and the internal representation
    #     used to compute the position of the specimen.
    #
    #     :param width: The width of the image representation in the GUI
    #     :param height: The height of the image representation in the GUI
    #     :param x: The center of the roi in the first dimension
    #     :param y: The center of the roi in the second dimension
    #     :param roi_width: The width of the ROI
    #     :param roi_height: The height of the ROI
    #     """
    #     if hasattr(self, 'tracker'):
    #         horizontal_scaling_factor, vertical_scaling_factor = self.__get_scaling_factors(width, height)
    #
    #         radius = diameter / 2.0
    #         scaled_x = (x + radius) * horizontal_scaling_factor
    #         scaled_y = (y + radius) * vertical_scaling_factor
    #         scaled_width = width * horizontal_scaling_factor
    #         scaled_height = height * horizontal_scaling_factor
    #
    #         self.roi = Circle((scaled_x, scaled_y), scaled_radius)

    def __get_scaled_roi_rectangle(self, source_type, img_width, img_height, roi_x, roi_y, roi_width, roi_height):
        horizontal_scaling_factor, vertical_scaling_factor = self.__get_scaling_factors(img_width, img_height)
        if 'ellipse' in source_type.lower():
            scaled_x = (roi_x + roi_width / 2) * horizontal_scaling_factor
            scaled_y = (roi_y + roi_height / 2) * vertical_scaling_factor
        elif 'rectangle' in source_type.lower():
            scaled_x = roi_x * horizontal_scaling_factor
            scaled_y = roi_y * vertical_scaling_factor
        else:
            raise NotImplementedError("Unknown ROI shape: {}".format(source_type))  # FIXME: change exception type
        scaled_width = roi_width * horizontal_scaling_factor
        scaled_height = roi_height * horizontal_scaling_factor
        # print("Roi after scaling: ({:.0f}, {:.0f}) width: {:.0f}, height: {:.0f}"
        #       .format(scaled_x, scaled_y, scaled_width, scaled_height))
        return scaled_x, scaled_y, scaled_width, scaled_height
        
    @pyqtSlot(QVariant, QVariant, QVariant, QVariant, QVariant, QVariant, QVariant, QVariant)
    def set_roi(self, roi_type, source_type, img_width, img_height, roi_x, roi_y, roi_width, roi_height):
        """
        Sets the ROI (in which to check for the specimen) from the one drawn in QT
        Scaling is applied to match the (resolution difference) between the representation 
        of the frames in the GUI (on which the user draws the ROI) and the internal representation
        used to compute the position of the specimen.

        :param str roi_type: The type of roi, one of ("tracking", "restriction")
        :param str source_type: The string representing the source type
        :param float img_width: The width of the image representation in the GUI
        :param float img_height: The height of the image representation in the GUI
        :param float roi_x: The center of the roi in the first dimension
        :param float roi_y: The center of the roi in the second dimension
        :param float roi_width: The width of the ROI
        :param float roi_height: The height of the ROI
        """

        source_type = str(source_type)
        source_type = self.__qurl_to_str(source_type)
        if hasattr(self, 'tracker'):
            print(roi_type, source_type, img_width, img_height, roi_x, roi_y, roi_width, roi_height)
            scaled_coords = self.__get_scaled_roi_rectangle(source_type, img_width, img_height,
                                                            roi_x, roi_y, roi_width, roi_height)
            if 'rectangle' in source_type.lower():
                roi = Rectangle(*scaled_coords)
            elif 'ellipse' in source_type.lower():
                roi = Ellipse(*scaled_coords)
            else:
                raise NotImplementedError("Unknown ROI shape: {}".format(source_type))
            self.__assign_roi(roi_type, roi)
        else:
            print("No tracker available")

    def __qurl_to_str(self, url):
        url = url.replace("PyQt5.QtCore.QUrl(u", "")
        url = url.strip(")\\'")
        return url

    def __assign_roi(self, roi_type, roi):
        if roi_type == 'tracking':
            self.roi = roi
        elif roi_type == 'restriction':
            self.tracking_region_roi = roi
        else:
            NotImplementedError("Unknown ROI type: {}".format(roi_type))

    @pyqtSlot(QVariant, QVariant)
    def set_roi_from_points(self, roi_type, points):
        if hasattr(self, 'tracker'):
            exp = re.compile('\d+')
            points = points.split("),")
            points = [map(float, exp.findall(p)) for p in points]
            roi = FreehandRoi(points)
            print(roi, points)
            self.__assign_roi(roi_type, roi)

    @pyqtSlot(QVariant)
    def remove_roi(self, roi_type):
        self.__assign_roi(roi_type, None)

    @pyqtSlot(QVariant)
    def save(self, default_dest):
        """
        Save the data (positions and distancesFromArena) as a csv style file
        """
        diag = QFileDialog()
        if default_dest:
            default_dest = os.path.splitext(default_dest)[0] + '.csv'
        else:
            default_dest = os.getenv('HOME')
        dest_path = diag.getSaveFileName(parent=diag,
                                         caption='Save file',
                                         directory=default_dest,
                                         filter="Text (*.txt *.dat *.csv)",
                                         initialFilter="Text (*.csv)")
        dest_path = dest_path[0]
        if dest_path:
            self.write(dest_path)
    
    def write(self, dest):
        """
        The method called by save() to write the csv file
        """
        with open(dest, 'w') as outFile:
            writer = csv.writer(outFile, delimiter=',', quotechar='|', quoting=csv.QUOTE_MINIMAL)
            for fid, row in enumerate(self.positions):
                writer.writerow([fid]+list(row))

    @pyqtSlot(QVariant)
    def set_frame_type(self, output_type):
        """
        Set the type of frame to display. (As source, difference with background or binary mask)
        
        :param string output_type: The type of frame to display. One of ['Raw', 'Diff', 'Mask']
        """
        self.output_type = output_type.lower()

    @pyqtSlot()
    def analyse_angles(self):
        """
        Compute and plot the angles between the segment Pn -> Pn+1 and Pn+1 -> Pn+2
        """
        if hasattr(self, 'tracker'):
            fig, ax = plt.subplots()
            angles = video_analysis.get_angles(self.positions)
            video_analysis.plot_angles(angles, self.get_sampling_freq())
            self.analysis_image_provider._fig = fig

    @pyqtSlot()
    def analyse_distances(self):
        """
        Compute and plot the distances between the points Pn and Pn+1
        """
        if hasattr(self, 'tracker'):
            fig, ax = plt.subplots()
            distances = video_analysis.pos_to_distances(self.positions)
            video_analysis.plot_distances(distances, self.get_sampling_freq())
            self.analysisImageProvider2._fig = fig

    @pyqtSlot()
    def save_angles_fig(self):
        """
        Save the graph as a png or jpeg image
        """
        if hasattr(self, 'tracker'):
            diag = QFileDialog()
            dest_path = diag.getSaveFileName(parent=diag,
                                             caption='Save file',
                                             directory=os.getenv('HOME'),
                                             filter="Image (*.png *.jpg)")
            dest_path = dest_path[0]
            if dest_path:
                imsave(dest_path, self.analysis_image_provider.get_array())

    def get_sampling_freq(self):
        return self.tracker._stream.fps

    def get_img(self):
        if self.tracker._stream.current_frame_idx < self.n_frames:
            self.display.reload()
            self._update_display_idx()
        else:
            self._stop('End of recording reached')


class RecorderIface(TrackerIface):
    """
    This class extends the TrackerIface to provide video acquisition and live detection (tracking).
    It uses openCV to run the camera and thus should work with any camera supported by openCV.
    It uses the first available USB/firewire camera unless the platform is a raspberry pi,
    in which case it will use the pi camera.
    """

    @pyqtSlot()
    def load(self):  # TODO: check if worth keeping
        pass
        
    @pyqtSlot(result=QVariant)
    def start(self):
        """
        Start the recording and tracking.
        
        :returns: The recording was started status code
        """
        if not hasattr(self.params, 'dest_path'):
            return False
        vid_ext = os.path.splitext(self.params.dest_path)[1]
        if vid_ext not in VIDEO_FORMATS:
            print('Unknow format: {}'.format(vid_ext))
            return False
        
        self.positions = []  # reset between runs
        self.distances_from_arena = []
        
        bg_start = self.params.bg_frame_idx
        n_background_frames = self.params.n_bg_frames
        track_from = self.params.start_frame_idx
        track_to = self.params.end_frame_idx if (self.params.end_frame_idx > 0) else None
        
        threshold = self.params.detection_threshold
        min_area = self.params.objects_min_area
        max_area = self.params.objects_max_area
        teleportation_threshold = self.params.teleportation_threshold
        
        n_sds = self.params.n_sds
        clear_borders = self.params.clear_borders
        normalise = self.params.normalise
        extract_arena = self.params.extract_arena

        self.tracker = Tracker(self, src_file_path=None, dest_file_path=self.params.dest_path,
                                  threshold=threshold, min_area=min_area, max_area=max_area,
                                  teleportation_threshold=teleportation_threshold,
                                  bg_start=bg_start, track_from=track_from, track_to=track_to,
                                  n_background_frames=n_background_frames, n_sds=n_sds,
                                  clear_borders=clear_borders, normalise=normalise,
                                  plot=True, fast=True, extract_arena=extract_arena,
                                  camera_calibration=self.params.calib,
                                  callback=None)
        self.stream = self.tracker  # to comply with BaseInterface
        self._set_display()
        self._update_img_provider()
        
        self.tracker.set_roi(self.roi)
        
        self.timer.start(self.timer_speed)
        return True
        
    def get_sampling_freq(self):
        """
        Return the sampling frequency (note this is a maximum and can be limited by a slower CPU)
        """
        return 1.0 / (self.timer_speed / 1000.0)  # timer speed in ms
        
    @pyqtSlot(result=QVariant)
    def cam_detected(self):
        """
        Check if a camera is available
        """
        cap = cv2.VideoCapture(0)
        detected = False
        if cap.isOpened():
            detected = True
        cap.release()
        return detected

    def get_img(self):
        self.display.reload()


class ParamsIface(QObject):
    """
    The QObject derived class that stores most of the parameters from the graphical interface
    for the other QT interfaces
    """
    def __init__(self, app, context, parent):
        """
        :param app: The QT application
        :param context:
        :param parent: the parent window
        """
        QObject.__init__(self, parent)
        self.app = app  # necessary to avoid QPixmap bug: Must construct a QGuiApplication before
        self.win = parent
        self.ctx = context
        self._set_defaults()
        
        self.calib = None

    def _set_defaults(self):
        """
        Reset the parameters to default.
        To customise the defaults, users should do this here.
        """
        self.bg_frame_idx = 5
        self.n_bg_frames = 1
        self.start_frame_idx = self.bg_frame_idx + self.n_bg_frames
        self.end_frame_idx = -1
        
        self.detection_threshold = 50
        self.objects_min_area = 100
        self.objects_max_area = 5000
        self.teleportation_threshold = 10000
        self.n_sds = 5.0
        
        self.clear_borders = False
        self.normalise = False
        self.extract_arena = False

    def __del__(self):
        """
        Reset the standard out on destruction
        """
        sys.stdout = sys.__stdout__

    @pyqtSlot(str)
    def set_tracker_type(self, tracker_type):
        try:
            tracker_class = TRACKER_CLASSES[tracker_type]
            globals()["Tracker"] = tracker_class
        except KeyError:
            print("Type must be one of {}, got: {}".format(TRACKER_CLASSES.keys(), tracker_type))

    @pyqtSlot()
    def chg_cursor(self):
        self.app.setOverrideCursor(Qt.CursorShape(Qt.CrossCursor))

    @pyqtSlot()
    def restore_cursor(self):
        self.app.restoreOverrideCursor()
    
    # BOOLEAN OPTIONS
    @pyqtSlot(bool)
    def set_clear_borders(self, status):
        self.clear_borders = status

    @pyqtSlot(result=bool)
    def get_clear_borders(self):
        return self.clear_borders

    @pyqtSlot(bool)
    def set_normalise(self, status):
        self.normalise = status

    @pyqtSlot(result=bool)
    def get_normalise(self):
        return self.normalise

    @pyqtSlot(bool)
    def set_extract_arena(self, status):
        self.extract_arena = status

    @pyqtSlot(result=bool)
    def get_extract_arena(self):
        return self.extract_arena

    # DETECTION OPTIONS
    @pyqtSlot(result=QVariant)
    def get_detection_threshold(self):
        return self.detection_threshold

    @pyqtSlot(QVariant)
    def set_detection_threshold(self, threshold):
        thrsh = int(threshold)
        if 0 < thrsh < 256:
            self.detection_threshold = thrsh

    @pyqtSlot(result=QVariant)
    def get_min_area(self):
        return self.objects_min_area

    @pyqtSlot(QVariant)
    def set_min_area(self, area):
        area = int(area)
        if area > 0:
            self.objects_min_area = area

    @pyqtSlot(result=QVariant)
    def get_max_area(self):
        return self.objects_max_area

    @pyqtSlot(QVariant)
    def set_max_area(self, area):
        area = int(area)
        if area > 0:
            self.objects_max_area = area

    @pyqtSlot(result=QVariant)
    def get_max_movement(self):
        return self.teleportation_threshold

    @pyqtSlot(QVariant)
    def set_max_movement(self, movement):
        mvmt = int(movement)
        if mvmt > 0:
            self.teleportation_threshold = mvmt

    @pyqtSlot(result=QVariant)
    def get_n_sds(self):
        return self.n_sds

    @pyqtSlot(QVariant)
    def set_n_sds(self, n):
        n_sds = int(n)
        if n_sds > 0:
            self.n_sds = n_sds

    # FRAME OPTIONS
    @pyqtSlot(QVariant)
    def set_bg_frame_idx(self, idx):
        idx = int(idx)
        idx = idx if idx >= 0 else 0
        max_idx = self.start_frame_idx - self.n_bg_frames
        self.bg_frame_idx = min(idx, max_idx)
    
    @pyqtSlot(result=QVariant)
    def get_bg_frame_idx(self):
        return self.bg_frame_idx

    @pyqtSlot(QVariant)
    def set_n_bg_frames(self, n):
        self.n_bg_frames = int(n)
    
    @pyqtSlot(result=QVariant)
    def get_n_bg_frames(self):
        return self.n_bg_frames

    @pyqtSlot(QVariant)
    def set_start_frame_idx(self, idx):
        idx = int(idx)
        min_idx = (self.bg_frame_idx + self.n_bg_frames)
        self.start_frame_idx = max(min_idx, idx)

    @pyqtSlot(result=QVariant)
    def get_start_frame_idx(self):
        return self.start_frame_idx

    @pyqtSlot(QVariant)
    def set_end_frame_idx(self, idx):
        idx = int(idx)
        if idx > 0:
            if idx <= self.start_frame_idx:
                idx = self.start_frame_idx + self.n_bg_frames
        else:
            idx = -1
        self.end_frame_idx = idx

    @pyqtSlot(result=QVariant)
    def get_end_frame_idx(self):
        return self.end_frame_idx

    @pyqtSlot(result=QVariant)
    def open_video(self):
        """The QT dialog to select the video to be used for preview or tracking"""
        if hasattr(self, 'src_path'):  # FIXME: should check vs None
            src_dir = os.path.dirname(self.src_path)
        else:
            src_dir = os.getenv('HOME')
        diag = QFileDialog()
        path = diag.getOpenFileName(parent=diag,
                                    caption='Open file',
                                    directory=src_dir,
                                    filter=VIDEO_FILTERS)
        src_path = path[0]
        if src_path:
            self.src_path = src_path
            self._set_defaults()
            return src_path

    @pyqtSlot(QVariant, result=QVariant)
    def set_save_path(self, path):
        """
        The QT dialog to select the path to save the recorded video
        """
        diag = QFileDialog()
        if not path:
            path = diag.getSaveFileName(parent=diag,
                                        caption='Save file',
                                        directory=os.getenv('HOME'),
                                        filter=VIDEO_FILTERS, 
                                        initialFilter="Videos (*.avi)")
            dest_path = path[0]
        else:
            dest_path = path if (os.path.splitext(path)[1] in VIDEO_FORMATS) else ""
        if dest_path:
            self.dest_path = dest_path
            return dest_path

    @pyqtSlot(result=QVariant)
    def is_path_selected(self):
        return hasattr(self, "src_path")

    @pyqtSlot(result=QVariant)
    def get_path(self):
        return self.src_path if self.is_path_selected() else ""

    @pyqtSlot(result=QVariant)
    def get_file_name(self):
        path = self.get_path()
        return os.path.basename(path) if path else ""
    
    @pyqtSlot(result=QVariant)
    def get_dest_path(self):
        return self.dest_path if hasattr(self, "dest_path") else ""


class EditorIface(QObject):
    def __init__(self, app, context, parent):
        """
        :param app: The QT application
        :param context:
        :param parent: the parent window
        """
        QObject.__init__(self, parent)
        self.app = app  # necessary to avoid QPixmap bug: Must construct a QGuiApplication before
        self.win = parent
        self.ctx = context

        self.src_path = None

        self.plugin_dir = os.getenv('HOME')  # FIXME:
        self.plugins = TRACKER_CLASSES  # FIXME: see if can improve

    @pyqtSlot(result=str)
    def open_plugin_code(self):
        diag = QFileDialog()
        path = diag.getOpenFileName(parent=diag,
                                    caption='Open file',
                                    directory=self.plugin_dir,
                                    filter="*.py")
        src_path = path[0]
        if src_path:
            self.src_path = src_path
            with open(src_path, 'r') as in_file:
                code = in_file.read()
            return code
        else:
            return ''

    @pyqtSlot(str)
    def save_plugin_code(self, src_code):
        diag = QFileDialog()
        path = diag.getSaveFileName(parent=diag,
                                    caption='Save file',
                                    directory=self.plugin_dir,
                                    filter="*.py")
        dest_path = path[0]
        if not dest_path.endswith('.py'):
            dest_path += '.py'
        if dest_path:
            with open(dest_path, 'w') as out_file:
                out_file.write(src_code)

    @pyqtSlot(str, result=str)
    def export_code_to_plugins(self, code):
        code_lines = code.split("\n")
        if [l.startswith('class') for l in code_lines].count(True) > 1:  # We don't know how to handle more than one
            return ''
        for l in code_lines:
            if l.startswith('class'):
                class_name = l.split(' ')[1]
                class_name = class_name.split('(')[0].strip()
                break
        else:
            return ''

        plugin = imp.new_module(class_name)
        exec code in plugin.__dict__
        cls = getattr(plugin, class_name)
        self.plugins[class_name] = cls
        return class_name

    @pyqtSlot(result=str)
    def load_plugin_template(self):
        with open(os.path.join(self.plugin_dir, 'template.py'), 'r') as in_file:
            template_code = in_file.read()
        return template_code
