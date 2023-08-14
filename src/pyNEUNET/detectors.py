"""
tcp_reader.py
Sabine Chu
08-01-2023

Implements basic packet reader
"""

import socket
import numpy as np
import matplotlib.pyplot as plt
from collections import OrderedDict
from .translaters import instrument_time, translate_neutron_data, to_physical_position
from .communications import register_readwrite, read_full_register

class Linear3HePSD:
    '''
    Linear 3He position sensitive neutron detector with a resolution of 5 mm
    built by Canon. The device is controlled with the NUENET system. 
    '''
    BINS = 1024
    NEUTRON_EVENT = 0x5f
    TRIGGER_ID = 0x5b
    INST_TIME = 0x6c
    START_BYTES = [NEUTRON_EVENT, TRIGGER_ID, INST_TIME]
    
    def __init__(self, ip_address="192.168.0.17", tcp_port=23, udp_port=4660, psd_nums=[0, 7], exposure_time=10):
        """
        Creates new socket object linked to detectors
        Default inputs given by Canon documentation for TCP connection

        Parameters
        -------
        ip_address: str, optional
                Remote address of detector
        tcp_port: int, optional
                Port number for TCP connection
        udp_port: int, optional
                Port number for UDP connection
        """
        self.name = "Linear3HePSD"
        self.parent = None
        self.__ip = ip_address
        self.__tcp_port = tcp_port
        self.__udp_port = udp_port
        self.__psd_nums = psd_nums
        self.__tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
        self.__tcp_sock.settimeout(5)
        self.__udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.__staged = False
        self.__exposure_time = exposure_time

    def stage(self, verbose=False):
        '''
        Sets up the NEUNET system register (mode, instrument time, etc) using UDP protocol.
        '''
        #Set time to 32-bit mode
        responseByte = register_readwrite(IP=self.__ip,port=self.__udp_port,address=0x18a,data=0x80)

        #First send the computer time to the instrument
        if verbose:
            print(f"Detector time before setting: \
                  {instrument_time(register_readwrite(IP=self.__ip,port=self.__udp_port,address=0x190, length=5)[8:], mode='datetime')}")
        # print(type(instrument_time()+bytes([0x00, 0x00])))
        responseByte = register_readwrite(IP=self.__ip,port=self.__udp_port,address=0x190, data=instrument_time()+bytes([0x00,0x00]))
        if verbose:
            print(f"Detector time after setting: \
                  {instrument_time(register_readwrite(IP=self.__ip,port=self.__udp_port,address=0x190, length=5)[8:], mode='datetime')}")

        #Set event memory read mode
        responseByte = register_readwrite(IP=self.__ip,port=self.__udp_port,address=0x186, data=bytes(2))

        #Set one-way mode and 14-bit (high-resolution) mode
        responseByte = register_readwrite(IP=self.__ip,port=self.__udp_port,address=0x1b4, data=[0x8a, 0x80])

        self.__staged = True
        if verbose:
            print("Finished staging")

    def unstage(self):
        '''
        Shuts down and clears the NEUNET system register.
        '''

        #Set handshake mode
        responseByte = register_readwrite(IP=self.__ip,port=self.__udp_port,address=0x1b5, data=[0x00])

        self.__staged = False
        # need more

    def collect_8bytes(self, offset=False, verbose=False):
        """
        Collect 8 bytes from already connected detector
        Bytes collected 1-by-1 due to socket's unpredictability re length of data

        Parameters
        -------
        offset: boolean, optional
                True if the data may not start with preferred byte values
                Default is False
        verbose: boolean, optional
                Whether to print out data as it comes in
        """
        if offset:
            recv_byte = self.__tcp_sock.recv(1)
            if verbose:
                print(recv_byte)
            while recv_byte[0] not in self.START_BYTES:
                recv_byte = self.__tcp_sock.recv(1)
                if verbose:
                    print(recv_byte.hex())
            self.__bytes_data = recv_byte
            for i in range(7):
                self.__bytes_data += self.__tcp_sock.recv(1)
            if recv_byte[0] == self.INST_TIME:
                self.__start_time = instrument_time(recv_byte)
        else:
            self.__bytes_data = bytes()
            for i in range(8):
                self.__bytes_data += self.__tcp_sock.recv(1)
        if verbose:
            print("Data: " + self.__bytes_data.hex(":"))

    def _count_neutron(self):
        """
        Counts the neutron event and adds it to the histogram
        """
        psd_number, position = translate_neutron_data(self.__bytes_data)
        if position is not None:
            res = self.BINS-1 if position*self.BINS >= self.BINS else int(position*self.BINS)
            self.__counts[f"detector {psd_number}"] += 1
            self.__histograms[f"detector {psd_number}"][1][res] += 1

    def read(self, test_label="", format="bluesky", graph=False, save=False, verbose=False, fldr=""):
        """"
        Connects to detector and reads data for given time length
        Creates histograms of neutron counts for binned physical positions on detectors
        
        Parameters
        -------
        test_label: str
                Name of files and graph
        format: str, optional
                Format of output
                If "bluesky" (default), output is a bluesky-compatible OrderedDict containing histograms and elapsed time
                Otherwise, output is a tuple containing start time, elapsed time, and histograms
        save: boolean, optional
                Whether to graph histogram data
                Default is False
        save: boolean, optional
                Whether to save histogram data and graph onto computer
                Default is False
        verbose: boolean, optional
                Whether to print indications that code is working
                Default is False
        fldr: str, optional
                Folder to save data to
                Only called if save is True
                Default is main directory

        Returns
        -------
        result: OrderedDict or tuple
                Stores histograms for each detector and start time for data collection
        """
        self.__tcp_sock.connect((self.__ip, self.__tcp_port))
        if verbose:
            print("Connected")
        blank_array = np.array([[to_physical_position(i/self.BINS) for i in range(self.BINS)],
                                [0 for i in range(self.BINS)]])
        self.__counts = {}
        self.__histograms = {}
        for i in self.__psd_nums:
            self.__counts[f"detector {i}"] = 0
            self.__histograms[f"detector {i}"] = np.copy(blank_array)
        self.__start_time = 0
        current_time = 0
        
        if not self.__staged:
            self.stage(verbose)
        self.collect_8bytes(offset=True)
        if verbose:
            print("Started collecting")
        while not self.__start_time:
            self.collect_8bytes()
            if self.__bytes_data[0] == self.INST_TIME:
                self.__start_time = instrument_time(self.__bytes_data[1:])
        if verbose:
            print("Reached first 'instrument time' data")
        while current_time - self.__start_time < self.__exposure_time:
            self.collect_8bytes()
            if self.__bytes_data[0] == self.NEUTRON_EVENT:
                self._count_neutron()
            elif self.__bytes_data[0] == self.INST_TIME:
                current_time = instrument_time(self.__bytes_data[1:])
        elapsed_time = current_time - self.__start_time
        if verbose:
            print(f"Completed collecting neutron counts")
            for i in self.__psd_nums:
                  print(f"Total counts from detector {i}: {self.__counts[f'detector {i}']}")

            print(f"Exposure time: {elapsed_time} s")
        self.__tcp_sock.close()

        if graph:
            fig, (ax0) = plt.subplots(1, 1)
            for i in self.__psd_nums:
                ax0.plot(self.__histograms[f"detector {i}"][0], self.__histograms[f"detector {i}"][1], label=f"detector {i}")
            ax0.legend()
            ax0.set_xlabel("position (mm)")
            ax0.set_ylabel("neutron count")
            ax0.set_title(f"Neutron counts v. position")

        if save:
            if fldr:
                if fldr[-1] != "/":
                    fldr += "/"
                test_label = fldr + test_label
            for i in self.__psd_nums:
                np.savetxt(f"{test_label}_detector{i}_histogram.txt", self.__histograms[f"detector {i}"],
                           header=f"detector {i}, start: {self.__start_time}; \
                            column 1 = physical position (mm), column 2 = counts per position.")
            if graph:
                fig.savefig(test_label+"_graph.png")
                fig.show()

        if format == "bluesky":
            # Timestamp is in the format of seconds since 1970
            start_timestamp = instrument_time(self.__start_time).timestamp()
            # TODO: The "value" field has to be JSON encodable (for our purposes, a number, string, or array)
            result = OrderedDict()
            for i in self.__psd_nums:
                result[f"detector {i}"] = {"value": self.__histograms[f"detector {i}"],
                                           "timestamp": start_timestamp}
            result["elapsed time"] = {"value": elapsed_time,
                                      "timestamp": start_timestamp}
            if verbose:
                print(result)
            return result
        
        return start_timestamp, elapsed_time, self.__histograms
    
    def describe(self):
        """
        Returns bluesky-compatible OrderedDict describing output data
        """
        description = OrderedDict()
        for i in self.__psd_nums:
            description[f"detector {i}"] = {"source": f"detector {i}",
                                            "dtype": "array",
                                            "shape": [self.BINS, 2]}
        description["elapsed time"] = {"source": "n/a",
                                       "dtype": "number",
                                       "shape": []}
        return description

    def sanity_check(self, pings=100):
        """
        Prints out given number of packets

        Parameters
        -------
        pings: int, optional
                Number of packets to print
                Default is 100
        """
        self.__tcp_sock.connect((self.__ip, self.__tcp_port))
        print("Connected")
        if not self.__staged:
            self.stage(True)
        self.collect_8bytes(offset=False)
        print(f"Bytes format: {self.__bytes_data}")
        print(f"Hexadecimal: {self.__bytes_data.hex(':')}")
        for i in range(pings-1):
            self.collect_8bytes()
            print(f"Bytes format: {self.__bytes_data}")
            print(f"Hexadecimal: {self.__bytes_data.hex(':')}")
            if self.__bytes_data[0] == self.NEUTRON_EVENT:
                print(f"Neutron data: {translate_neutron_data(self.__bytes_data)}")
            elif self.__bytes_data[0] == self.INST_TIME:
                print(f"Instrument time: {instrument_time(self.__bytes_data[1:], mode='datetime')}")
        self.__tcp_sock.close()
        print("Closed socket")
    
    @property
    def exposure_time(self):
        return self.__exposure_time
    
    @exposure_time.setter
    def exposure_time(self, input):
        self.__exposure_time = input


def main():
    obj = Linear3HePSD()
    # obj.sanity_check()
    # obj.exposure_time = 60
    # output = obj.read(test_label="8_14_2023_try", graph=True, save=True, fldr="c:/Users/4DH4/Desktop/pyneunet_output", verbose=True)
    # print(output)

if __name__ == '__main__':
    main()