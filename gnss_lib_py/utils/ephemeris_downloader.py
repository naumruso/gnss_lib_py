"""Functions to download, save and process satellite ephemeris files.

The Ephemeris Manager provides broadcast ephemeris for specific
satellites at a specific timestep. The EphemerisDownloader class should be
initialized and then the ``get_ephemeris`` function can be used to
retrieve ephemeris for specific satellites. ``get_ephemeris`` returns
the most recent broadcast ephemeris for the provided list of satellites
that was broadcast BEFORE the provided timestamp. For example GPS daily
ephemeris files contain data at a two hour frequency, so if the
timestamp provided is 5am, then ``get_ephemeris`` will return the 4am
data but not 6am. If provided a timestamp between midnight and 2am then
the ephemeris from around midnight (might be the day before) will be
provided. If no list of satellites is provided, then ``get_ephemeris``
will return data for all satellites.

When multiple observations are provided for the same satellite and same
timestep, the Ephemeris Manager will only return the first instance.
This is applicable when requesting ephemeris for multi-GNSS for the
current day. Same-day multi GNSS data is pulled from  same day. For
same-day multi-GNSS from https://igs.org/data/ which often has multiple
observations.

"""

__authors__ = "Shubh Gupta, Ashwin Kanhere"
__date__ = "13 July 2021"

import os
import shutil
import gzip
import ftplib
from ftplib import FTP_TLS, FTP
from datetime import datetime, timezone

import unlzw3

from gnss_lib_py.utils.time_conversions import tzinfo_to_utc

DEFAULT_EPHEM_PATH = os.path.join(os.getcwd(), 'data', 'ephemeris')

class EphemerisDownloader():
    """Download, store and process ephemeris files

    Attributes
    ----------
    ephemeris_directory : string
        Directory to store/read ephemeris files
    data : pd.Dataframe
        Ephemeris parameters
    leapseconds : int
        Leap seconds to add to UTC time to get GPS time
    verbose : bool
        If true, prints debugging statements.

    Notes
    -----
    Class code taken from https://github.com/johnsonmitchelld/gnss-analysis/blob/main/gnssutils/ephemeris_manager.py

    The associated license is copied below:

    BSD 3-Clause License

    Copyright (c) 2021, Mitchell D Johnson
    All rights reserved.

    Redistribution and use in source and binary forms, with or without
    modification, are permitted provided that the following conditions are met:

    1. Redistributions of source code must retain the above copyright notice, this
       list of conditions and the following disclaimer.

    2. Redistributions in binary form must reproduce the above copyright notice,
       this list of conditions and the following disclaimer in the documentation
       and/or other materials provided with the distribution.

    3. Neither the name of the copyright holder nor the names of its
       contributors may be used to endorse or promote products derived from
       this software without specific prior written permission.

    THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
    AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
    IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
    DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
    FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
    DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
    SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
    CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
    OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
    OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

    """
    def __init__(self, ephemeris_directory=DEFAULT_EPHEM_PATH,
                 verbose=False):
        self.ephemeris_directory = ephemeris_directory
        nasa_dir = os.path.join(ephemeris_directory, 'nasa')
        igs_dir = os.path.join(ephemeris_directory, 'igs')
        os.makedirs(nasa_dir, exist_ok=True)
        os.makedirs(igs_dir, exist_ok=True)
        self.data = None
        self.leapseconds = None
        self.iono_params = None
        self.verbose = verbose

    def get_ephemeris(self, timestamp, satellites=None):
        """Return ephemeris DataFrame for satellites input.

        Downloads Rinex files based on satellites and timestamp. If
        ``satellites`` is None, then Rinex file for all possible
        satellites will be downloaded.

        Parameters
        ----------
        timestamp : datetime.datetime
            Ephemeris data is returned for the timestamp day and
            includes all broadcast ephemeris whose broadcast timestamps
            happen before the given timestamp variable. Timezone should
            be added manually and is interpreted as UTC if not added.
        satellites : List
            List of satellite IDs as a string, for example ['G01','E11',
            'R06']. Defaults to None which returns get_ephemeris for
            all satellites.

        Returns
        -------
        rinex_paths : list
            List of paths to decompressed rinex files.

        Notes
        -----
        The Galileo week ``GALWeek`` is identical to the GPS Week
        ``GPSWeek``. See http://acc.igs.org/misc/rinex304.pdf page A26

        """
        systems = EphemerisDownloader.get_constellations(satellites)
        # add UTC timezone if datatime os offset-naive
        timestamp = tzinfo_to_utc(timestamp)
        same_day = (datetime.now(timezone.utc) - timestamp).days <= 0
        rinex_paths = self.load_data(timestamp, systems, same_day)

        return rinex_paths

    def load_data(self, timestamp, constellations=None, same_day=False):
        """Load ephemeris into class instance

        Parameters
        ----------
        timestamp : datetime.datetime
            Ephemeris data is returned for the timestamp day and
            includes all broadcast ephemeris whose broadcast timestamps
            happen before the given timestamp variable. Timezone should
            be added manually and is interpreted as UTC if not added.
        constellations : Set
            Set of satellites For example, set({"G","R","E"}).
        same_day : bool
            Whether or not ephemeris is for same-day aquisition.

        Returns
        -------
        rinex_paths : list
            List of paths to decompressed rinex files.


        """
        filepaths = EphemerisDownloader.get_filepaths(timestamp)
        rinex_paths = []

        if constellations == None:
            for fileinfo in filepaths.values():
                rinex_path = self.get_rinex_path(fileinfo)
                rinex_paths.append(rinex_path)
        else:
            legacy_systems = set(['G', 'R'])
            legacy_systems_only = len(constellations - legacy_systems) == 0
            if not same_day:
                if legacy_systems_only:
                    if 'G' in constellations:
                        rinex_path = self.get_rinex_path(filepaths['nasa_daily_gps'])
                        rinex_paths.append(rinex_path)
                    if 'R' in constellations:
                        rinex_path = self.get_rinex_path(filepaths['nasa_daily_glonass'])
                        rinex_paths.append(rinex_path)
                else:
                    rinex_path = self.get_rinex_path(filepaths['nasa_daily_combined'])
                    rinex_paths.append(rinex_path)
            else:
                if legacy_systems_only and 'G' in constellations:
                    rinex_path = self.get_rinex_path(filepaths['nasa_daily_gps'])
                    rinex_paths.append(rinex_path)
                else:
                    rinex_path = self.get_rinex_path(filepaths['bkg_daily_combined'])
                    rinex_paths.append(rinex_path)

        return rinex_paths

    def get_rinex_path(self, fileinfo):
        """Returns decompressed filename from filepaths in get_filepaths. If
        the file does not already exist on the machine, the file is retrieved
        from the url specified in fileinfo.

        Parameters
        ----------
        fileinfo : dict
            Filenames for ephemeris with ftp server and constellation details

        Returns
        -------
        rinex_path : string
            Postprocessed filepath to decompressed rinex file
        """
        filepath = fileinfo['filepath']
        url = fileinfo['url']
        directory = os.path.split(filepath)[0]
        filename = os.path.split(filepath)[1]
        if url == 'igs-ftp.bkg.bund.de':
            dest_filepath = os.path.join(self.ephemeris_directory, 'igs', filename)
        else:
            dest_filepath = os.path.join(self.ephemeris_directory, 'nasa', filename)
        rinex_path = os.path.splitext(dest_filepath)[0]
        if not os.path.isfile(rinex_path): # pragma: no cover
            self.retrieve_file(url, directory, filename,
                               dest_filepath)

        return rinex_path

    @staticmethod
    def get_filetype(timestamp):
        """Get file extension of IGS file based on timestamp

        Parameters
        ----------
        timestamp : datetime.datetime
            Time of clock

        Returns
        -------
        extension : string
            Extension of compressed ephemeris file
        """
        # IGS switched from .Z to .gz compression format on December 1st, 2020
        if timestamp >= datetime(2020, 12, 1, 0, 0, 0, tzinfo=timezone.utc):
            extension = '.gz'
        else:
            extension = '.Z'
        return extension

    @staticmethod
    def get_constellations(satellites):
        """Convert list of satellites to set

        Parameters
        ----------
        satellites : List
            List of satellites of form [ConstIDSVID]

        Returns
        -------
        systems : Set or None
            Set representation of satellites for which ephemeris is needed
        """
        if isinstance(satellites, list):
            systems = set()
            for sat in satellites:
                systems.add(sat[0])
            return systems

        return None

    def retrieve_file(self, url, directory, filename, dest_filepath):
        """Copy ephemeris file from FTP filepath to local directory.

        Also decompresses file.

        Parameters
        ----------
        url : String
            FTP server location

        directory : String
            Directory where ephemeris files are stored on the FTP server

        filename : String
            Filename in which ephemeris files are stored (both locally and globally)

        dest_filepath : String
            Directory where downloaded ephemeris files are stored locally

        """

        secure = bool(url == 'gdc.cddis.eosdis.nasa.gov')

        if self.verbose:
            print('Retrieving ' + directory + '/' + filename + ' from ' + url)
        ftp = self.connect(url, secure)
        src_filepath = directory + '/' + filename
        try:
            with open(dest_filepath, 'wb') as handle:
                ftp.retrbinary(
                    'RETR ' + src_filepath, handle.write)
        except ftplib.error_perm as err:
            os.remove(dest_filepath)
            raise ftplib.error_perm(str(err) + ' Failed to retrieve ' \
                                  + src_filepath + ' from ' + url)

        ftp.quit()
        if ftp is not None: # try closing if still active
            ftp.close()
        self.decompress_file(dest_filepath)

    def decompress_file(self, filepath):
        """Decompress downloaded file ephemeris file in same destination location

        Parameters
        ----------
        filepath : String
            Local filepath where the compressed ephemeris file is stored

        """
        extension = os.path.splitext(filepath)[1]
        decompressed_path = os.path.splitext(filepath)[0]
        if extension == '.gz':
            with gzip.open(filepath, 'rb') as f_in:
                with open(decompressed_path, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
        elif extension == '.Z':
            with open(filepath, 'rb') as f_in:
                with open(decompressed_path, 'wb') as f_out:
                    f_out.write(unlzw3.unlzw(f_in.read()))
        os.remove(filepath)

    def connect(self, url, secure):
        """Connect to given FTP server

        Parameters
        ----------
        url : String
            URL of FTP server where ephemeris files are stored

        secure : Bool
            Flag for secure FTP connection

        Returns
        -------
        ftp : FTP_TLS
            FTP connection object
        """
        if secure:
            ftp = FTP_TLS(url)
            ftp.login()
            ftp.prot_p()
        else:
            ftp = FTP(url)
            ftp.login()
        return ftp

    @staticmethod
    def get_filepaths(timestamp):
        """Generate filepaths for all ephemeris files

        Parameters
        ----------
        timestamp : datetime.datetime
            Time of clock

        Returns
        -------
        filepaths : Dict
            Dictionary of dictionaries containing filepath and directory for ephemeris files
        """
        timetuple = timestamp.timetuple()
        extension = EphemerisDownloader.get_filetype(timestamp)
        filepaths = {}

        directory = 'gnss/data/daily/' + str(timetuple.tm_year) + '/brdc/'
        filename = 'BRDC00IGS_R_' + \
            str(timetuple.tm_year) + \
            str(timetuple.tm_yday).zfill(3) + '0000_01D_MN.rnx.gz'
        filepaths['nasa_daily_combined'] = {
            'filepath': directory + filename, 'url': 'gdc.cddis.eosdis.nasa.gov'}

        filename = 'brdc' + str(timetuple.tm_yday).zfill(3) + \
            '0.' + str(timetuple.tm_year)[-2:] + 'n' + extension
        filepaths['nasa_daily_gps'] = {
            'filepath': directory + filename, 'url': 'gdc.cddis.eosdis.nasa.gov'}

        filename = 'brdc' + str(timetuple.tm_yday).zfill(3) + \
            '0.' + str(timetuple.tm_year)[-2:] + 'g' + extension
        filepaths['nasa_daily_glonass'] = {
            'filepath': directory + filename, 'url': 'gdc.cddis.eosdis.nasa.gov'}

        directory = '/IGS/BRDC/' + \
            str(timetuple.tm_year) + '/' + \
            str(timetuple.tm_yday).zfill(3) + '/'
        filename = 'BRDC00WRD_S_' + \
            str(timetuple.tm_year) + \
            str(timetuple.tm_yday).zfill(3) + '0000_01D_MN.rnx.gz'
        filepaths['bkg_daily_combined'] = {
            'filepath': directory + filename, 'url': 'igs-ftp.bkg.bund.de'}

        return filepaths
