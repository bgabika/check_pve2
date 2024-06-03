#!/usr/bin/python3
# -*- coding: utf-8 -*-
# ---------------------------------------------------------------
# COREX Proxmox VE check plugin for Icinga 2
# Copyright (C) 2019-2024  Gabor Borsos <bg@corex.bg>
# 
# v1.25 built on 2024.06.03.
# usage: check_pve2.py --help
#
# For bugs and feature requests mailto bg@corex.bg
# 
# ---------------------------------------------------------------
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
# 
# Test it in test environment to stay safe and sensible before 
# using in production!
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
# ---------------------------------------------------------------
#
# changelog:
# 2024.06.03. v1.25  - PVE8 - Ignore the syslog service based on the deprecation in Debian 12.5
# 2024.04.01. v1.24  - Add ceph-io subcommand
# 2022.12.13. v1.23  - Add help
# 2022.12.13. v1.22  - Bugfix, storage Graphite performance output
# 2022.12.08. v1.21  - Bugfix, storage size zero division
# 2022.12.06. v1.2  - Bugfix, storage check unit bug
# 2022.10.27. v1.1  - Bugfix, disk-health CRITICAL check
# 2022.10.23. v1.0  - First release
# ---------------------------------------------------------------

import re, sys

try:
    from enum import Enum
    from datetime import datetime
    import argparse
    import requests
    import textwrap

except ImportError as e:
    print("Missing python module: {}".format(str(e)))
    sys.exit(255)


class CheckState(Enum):
    OK = 0
    WARNING = 1
    CRITICAL = 2
    UNKNOWN = 3


class CheckPVE:

    def __init__(self):
        self.API_URL = 'https://{hostname}:{port}/api2/json/{command}'
        self.result_list = []
        self.pluginname = "check_pve2.py"
        self.parse_args()
        self.__headers = {}
        self.__cookies = {}

        if self.options.api_insecure:
            requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)

        if self.options.api_password is not None:
            self.__cookies['PVEAuthCookie'] = self.get_ticket()
        elif self.options.api_token is not None:
            self.__headers["Authorization"] = "PVEAPIToken={}!{}".format(self.options.api_user, self.options.api_token)



    def parse_args(self):
        parser = argparse.ArgumentParser(
            prog=self.pluginname, 
            add_help=True, 
            formatter_class=argparse.RawTextHelpFormatter,
            description = textwrap.dedent("""
            PLUGIN DESCRIPTION: COREX PROXMOX check plugin for ICINGA 2."""),
            epilog = textwrap.dedent(f"""
            Examples:
            with api token
            {self.pluginname} --hostname pve.mydomain.com --api_user monitoring@pve --api_token A12fhaDFCjn92aKt=123f922a-e10b-12z7-e133-Aa3476b866ar --subcommand cpu --nodename pve1 --warning 65 --critical 85
            {self.pluginname} --hostname pve.mydomain.com --api_user monitoring@pve --api_token A12fhaDFCjn92aKt=123f922a-e10b-12z7-e133-Aa3476b866ar --subcommand cluster --nodename pve1
            {self.pluginname} --hostname pve.mydomain.com --api_user monitoring@pve --api_token A12fhaDFCjn92aKt=123f922a-e10b-12z7-e133-Aa3476b866ar --subcommand storage --nodename pve1 --warning 70 --critical 80 --ignore-disk vm-backup
            with api password:
            {self.pluginname} --hostname pve.mydomain.com --api_user monitoring@pve --api_password mypassword --subcommand storage --nodename pve1 --ignore-disk disk1 --ignore-disk disk2 --warning 80 --critical 85"""))

        api_connect_opt = parser.add_argument_group('API connection arguments', 'hostname, api_user, api_password, api_port')

        api_connect_opt.add_argument('--hostname', dest="api_host", type=str, required=True, help="host FQDN or IP")
        api_connect_opt.add_argument('--api_port', type=int, required=False, help="api port, default port: 8006", default=8006)
        api_connect_opt.add_argument('--api_user', type=str, required=True, help="api user")
        api_connect_opt.add_argument('--api_password', type=str, required=False, help="api password")
        api_connect_opt.add_argument('--api_token', type=str, required=False, help="api token, format: token_ID=secret")
        api_connect_opt.add_argument("--insecure", dest='api_insecure', action='store_true', default=True,
                              help="Don't verify HTTPS certificate")


        check_pve_opt = parser.add_argument_group('check arguments', 'ceph, ceph_io, cluster, cpu, disks_health, memory, pveversion, services, storage, swap')
        
        check_pve_opt.add_argument("--subcommand",
                                        choices=(
                                            'ceph', 'ceph_io', 'cluster', 'cpu', 'disks_health', 'memory', 'pveversion', 'services', 'storage', 'swap'),
                                        required=True,
                                        help="Select subcommand to use. Some subcommands need warning and critical arguments. \
                                            Disk subcommand needs warning and critical to check wearout state.")
        
        check_pve_opt.add_argument('--nodename', type=str, required=True, help="node name")
        
        check_pve_opt.add_argument('--ignore-disk', dest='ignore_disks', action='append', metavar='DISKNAME',
                                        help='Ignore disks in health check, --ignore-disk disk1 --ignore-disk disk2 ...etc', default=[])

        check_pve_opt.add_argument('--disk-name', dest='include_disks', action='append', metavar='DISKNAME',
                                        help='Check disks in health check by disk name, --disk-name disk1 --disk-name disk2 ...etc', default=[])
        
        check_pve_opt.add_argument('--ceph-io-warning', dest='ceph_io_warning', type=int,
                                help='IO read/write warning threshold for cheph-io checking. Default: 10000 operations/sec', default=10000)
        
        check_pve_opt.add_argument('--ceph-byte-warning', dest='ceph_byte_warning', type=int,
                                help='Byte read/write warning threshold for cheph-io checking. Default: 200MB/sec', default=200)

        check_pve_opt.add_argument('--warning', dest='threshold_warning', type=int,
                                help='Warning threshold for check value. Mutiple thresholds with name:value,name:value')
        
        check_pve_opt.add_argument('--critical', dest='threshold_critical', type=int,
                                help='Critical threshold for check value. Mutiple thresholds with name:value,name:value')

        self.options = parser.parse_args()

        if (self.options.subcommand == "cpu" or self.options.subcommand == "disks_health" or self.options.subcommand == "memory" or \
            self.options.subcommand == "storage" or self.options.subcommand == "swap") and \
            (self.options.threshold_warning is None or self.options.threshold_critical is None):
            
            parser.error(f"--warning and --critical arguments are required for '{self.options.subcommand}' subcommand!")
            
        if self.check_thresholds_scale("increase") == False:
            parser.error(f"--warning threshold must be lower then --critical threshold for '{self.options.subcommand}' subcommand!")
        elif self.check_thresholds_scale("decrease") == False:
            parser.error(f"--warning threshold must be higher then --critical threshold for '{self.options.subcommand}' subcommand!")



    def main(self):
        api_url = self.get_url(self.options.subcommand)
        request_output = self.request(api_url)
        
        eval(f"self.check_{self.options.subcommand}" + "(request_output, self.options.subcommand)")
        self.check_exitcodes(self.result_list)
    


    def get_url(self, apiurl):
        if apiurl == "cpu" or apiurl == "memory" or apiurl == "pveversion" or apiurl == "swap":
            return self.API_URL.format(hostname=self.options.api_host, port=self.options.api_port, command=f"nodes/{self.options.nodename}/status")
        elif apiurl == "disks_health":
            return self.API_URL.format(hostname=self.options.api_host, port=self.options.api_port, command=f"nodes/{self.options.nodename}/disks/list")
        elif apiurl == "ceph":
            return self.API_URL.format(hostname=self.options.api_host, port=self.options.api_port, command="cluster/ceph/status")
        elif apiurl == "ceph_io":
            return self.API_URL.format(hostname=self.options.api_host, port=self.options.api_port, command=f"nodes/{self.options.nodename}/ceph/status")
        elif apiurl == "cluster":
            return self.API_URL.format(hostname=self.options.api_host, port=self.options.api_port, command="cluster/status")
        elif apiurl == "storage":
            return self.API_URL.format(hostname=self.options.api_host, port=self.options.api_port, command=f"nodes/{self.options.nodename}/storage")
        elif apiurl == "services":
            return self.API_URL.format(hostname=self.options.api_host, port=self.options.api_port, command=f"nodes/{self.options.nodename}/services")
        else:
            return self.API_URL.format(hostname=self.options.api_host, port=self.options.api_port, command=apiurl)



    def get_ticket(self):
        url = self.get_url('access/ticket')
        data = {"username": self.options.api_user, "password": self.options.api_password}
        result = self.request(url, "post", data=data)

        return result['ticket']



    @staticmethod
    def output(state, message):
        prefix = state.name
        message = '{} - {}'.format(prefix, message)

        print(message)
        sys.exit(state.value)



    @staticmethod
    def check_UOM(mynumber):
        mynumber_lenght = len(str(mynumber))
        my_unit = "GB"
        if mynumber_lenght >= 13:
            mynumber = round(mynumber/1024**4, 2)
            my_unit = "TB"
            
        if mynumber_lenght >= 10 and mynumber_lenght <= 12:
            mynumber = round(mynumber/1024**3, 2)
            my_unit = "GB"

        if mynumber_lenght < 10:
            mynumber = round(mynumber/1024**2, 2)
            my_unit = "MB"

        return mynumber, my_unit



    @staticmethod
    def get_common_unit(used_number, total_number):
        total_number_lenght = len(str(total_number))
        my_unit = "GB"
        if total_number_lenght >= 13:
            total_number = round(total_number/1024**4, 2)
            used_number = round(used_number/1024**4, 2)
            my_unit = "TB"
            
        if total_number_lenght >= 10 and total_number_lenght <= 12:
            total_number = round(total_number/1024**3, 2)
            used_number = round(used_number/1024**3, 2)
            my_unit = "GB"

        if total_number_lenght < 10:
            total_number = round(total_number/1024**2, 2)
            used_number = round(used_number/1024**2, 2)
            my_unit = "MB"

        return used_number, total_number, my_unit



    def check_thresholds_scale(self, scale):
        if (self.options.subcommand == "cpu" or self.options.subcommand == "memory" or self.options.subcommand == "storage" or self.options.subcommand == "swap") and scale == "increase":
            return(self.options.threshold_warning < self.options.threshold_critical)
               
        elif self.options.subcommand == "disks_health" and scale == "decrease": 
            return(self.options.threshold_critical < self.options.threshold_warning)



    def check_ceph(self, perfdata, subcommand):
        
        if perfdata["health"]["status"] != "HEALTH_OK":
            self.output(CheckState.WARNING, "CEPH cluster is unhealthy!")
        else:
            self.output(CheckState.OK, "CEPH cluster is healthy.")


    def check_ceph_io(self, perfdata, subcommand):
        read_bytes_sec = round(int((perfdata["pgmap"]["read_bytes_sec"]))/1048576, 2)
        write_bytes_sec = round(int((perfdata["pgmap"]["write_bytes_sec"]))/1048576, 2)
        read_op_per_sec = int((perfdata["pgmap"]["read_op_per_sec"]))
        write_op_per_sec = int((perfdata["pgmap"]["write_op_per_sec"]))
        ceph_io_warning = self.options.ceph_io_warning
        ceph_byte_warning = self.options.ceph_byte_warning

        
        message = f"CEPH IO operation usage is {read_op_per_sec} ops read / {write_op_per_sec} ops write per seconds.\
        |'ceph io read per sec'={read_op_per_sec};{ceph_io_warning};;0; 'ceph io write per sec'={write_op_per_sec};{ceph_io_warning};;0;"
        
        if ceph_io_warning <= read_op_per_sec or ceph_io_warning <= write_op_per_sec:
            self.result_list.append(f"WARNING - {message}")
        else:
            self.result_list.append(f"OK - {message}")

    
        message = f"CEPH IO byte usage is {read_bytes_sec} MB read / {write_bytes_sec} MB write per seconds.\
        |'ceph byte read per sec'={read_bytes_sec};{ceph_byte_warning};;0; 'ceph byte write per sec'={write_bytes_sec};{ceph_byte_warning};;0;"
        
        if ceph_byte_warning <= read_bytes_sec or ceph_byte_warning <= write_bytes_sec:
            self.result_list.append(f"WARNING - {message}")
        else:
            self.result_list.append(f"OK - {message}")
        


    def check_cluster(self, perfdata, subcommand):
        offline_node_list = []
        node_details_dict = {}
        clustername = (perfdata[0]["name"])
        quorate = (perfdata[0]["quorate"])
        
        if quorate is None:
            self.output(CheckState.WARNING, "There is no cluster configuration!")

        else:
            if quorate == 1:
                nodelist = (perfdata[1:len(perfdata)])
                for node in nodelist:
                    if node["online"] == 1:
                        node_details_dict[node["name"]] = ["online", node["ip"]]
                    else:
                        node_details_dict[node["name"]] = ["offline", node["ip"]]
                
                for k,v in node_details_dict.items():
                    if v[0] != "online":
                        offline_node_list.append(k)
                    
                if len(offline_node_list) > 0:
                    self.output(CheckState.WARNING, f"{clustername} cluster are working, but there is offline nodes: {*offline_node_list,}!")
                else:
                    self.output(CheckState.OK, f"{clustername} cluster is working well.")
            else:
                self.output(CheckState.CRITICAL, f"There is no quorum in {clustername} cluster!")


    
    def check_cpu(self,request_output, subcommand):
        cpu_usage = round(((request_output["cpu"])*100), 2)

        message = f"CPU usage is {cpu_usage} %. |usage={cpu_usage}%;{self.options.threshold_warning};{self.options.threshold_critical};0;100"

        if self.options.threshold_critical <= cpu_usage:
            self.output(CheckState.CRITICAL, message)
        elif  self.options.threshold_warning <= cpu_usage and cpu_usage <= self.options.threshold_critical:
            self.output(CheckState.WARNING, message)
        elif cpu_usage < self.options.threshold_warning:
            self.output(CheckState.OK, message)



    def check_disks_health(self, request_output, subcommand):
        for disk in request_output:
            disk_vendor = (disk["vendor"]).strip()
            disk_model = disk["model"]
            disk_type = disk["type"]
            disk_devpath = disk["devpath"]
            disk_health = disk["health"]
            disk_wearout = disk["wearout"]
            
            if disk_health != "OK" and disk_health != "PASSED" and disk_health != "UNKNOWN":
                self.result_list.append(f"WARNING - {disk_vendor} - {disk_model} type: {disk_type} on {disk_devpath} is failed: {disk_health}")
            elif isinstance(disk_wearout, int) and disk_wearout <= self.options.threshold_warning and disk_wearout >= self.options.threshold_critical:
                self.result_list.append(f"WARNING - {disk_vendor} - {disk_model} type: {disk_type} on {disk_devpath} has low wearout: {disk_wearout}")
            elif isinstance(disk_wearout, int) and disk_wearout <= self.options.threshold_critical:
                self.result_list.append(f"CRITICAL - {disk_vendor} - {disk_model} type: {disk_type} on {disk_devpath} has low wearout: {disk_wearout}")
            else:
                if not any("WARNING" in x for x in self.result_list) or not any("CRITICAL" in x for x in self.result_list):
                    self.result_list.append(f"OK - All disks are healthy.")

            if any("WARNING" in x for x in self.result_list):
                self.result_list = [x for x in self.result_list if re.search("WARNING -", x) if re.search("CRITICAL -", x)]

        self.result_list = set(self.result_list)



    def check_memory(self, request_output, subcommand):
        
        memory_used = round((request_output[f"{subcommand}"]["used"])/1024**3, 1)
        memory_total = round((request_output[f"{subcommand}"]["total"])/1024**3, 1)
        memory_used_warning = round(((memory_total /100)*self.options.threshold_warning),2)
        memory_used_critical = round(((memory_total /100)*self.options.threshold_critical),2)


        if memory_total == 0:
            memory_used_percent = round((memory_used/1)*100,2)
        else:
            memory_used_percent = round((memory_used/memory_total)*100,2)
        
        message = f"{subcommand} usage is {memory_used_percent} % ({memory_used} GB / {memory_total} GB)!\
                |usage={memory_used}GB;{memory_used_warning};{memory_used_critical};0;{memory_total}"

        if self.options.threshold_critical <= memory_used_percent:
            self.output(CheckState.CRITICAL, message)
        elif  self.options.threshold_warning <= memory_used_percent and memory_used_percent <= self.options.threshold_critical:
            self.output(CheckState.WARNING, message)
        elif memory_used_percent < self.options.threshold_warning:
            self.output(CheckState.OK, message)



    def check_pveversion(self, request_output, subcommand):
        pveversion = ((request_output["pveversion"])).split("/")
        self.output(CheckState.OK, f"{pveversion[0]}/{pveversion[1]}")



    def check_services(self, request_output, subcommand):
        for element in request_output:
            service_name = (element["name"])
            service_desc = (element["desc"])
            service_unit_state = (element["unit-state"])
            service_state = (element["state"])
            service_active_state = (element["active-state"])


            if (service_state != "running" or service_active_state != "active") and service_unit_state != "not-found":
                self.result_list.append(f"WARNING - {service_desc} ({service_name}) is {service_state}.")
            else:
                if not any("WARNING" in x for x in self.result_list):
                    self.result_list.append(f"OK - All services are running.")

            if any("WARNING" in x for x in self.result_list):
                self.result_list = [x for x in self.result_list if re.search("WARNING -", x)]

        self.result_list = set(self.result_list)



    def check_storage(self, request_output, subcommand):
        
        def check_storage_inside():

            message = f"{storage_name} disk usage (type: {storage_type}) is {storage_used_percent} % ({storage_used} {storage_used_unit} / {storage_total} {storage_total_unit}).\
                        |{storage_name}={storage_common_used}{storage_common_unit};{storage_used_warning};{storage_used_critical};0;{storage_common_total}"
            
            if storage_enabled == 1:
                if storage_active != 1:
                    self.result_list.append(f"WARNING - {storage_name} disk is not active!")
                else:
                    if self.options.threshold_critical <= storage_used_percent:
                        self.result_list.append(f"CRITICAL - {message}")
                    elif  self.options.threshold_warning <= storage_used_percent and storage_used_percent <= self.options.threshold_critical:
                        self.result_list.append(f"WARNING - {message}")
                    elif storage_used_percent < self.options.threshold_warning:
                        self.result_list.append(f"OK - {message}")

        for storage in request_output:
            storage_name = storage["storage"]
            storage_enabled = storage["enabled"]
            storage_active = storage["active"]
            storage_type = storage["type"]
            storage_used_byte = storage["used"]
            storage_total_byte = storage["total"]
            try:
                storage_used_percent = round((storage_used_byte/storage_total_byte)*100,2)
            except:
                storage_used_percent = round((storage_used_byte/1)*100,2)
            storage_used, storage_used_unit = self.check_UOM(storage_used_byte)
            storage_total, storage_total_unit = self.check_UOM(storage_total_byte)
            storage_common_used, storage_common_total, storage_common_unit = self.get_common_unit(storage_used_byte, storage_total_byte)
            storage_used_warning = round(((storage_total / 100)*self.options.threshold_warning),2)
            storage_used_critical = round(((storage_total / 100)*self.options.threshold_critical),2)
            

            if len(self.options.include_disks) > 0:
                if storage_name in self.options.include_disks:
                    check_storage_inside()

            else:
                if storage_name not in self.options.ignore_disks:
                    check_storage_inside()


    
    def check_swap(self, request_output, subcommand):
        return self.check_memory(request_output, subcommand)



    def check_exitcodes(self, result_list):
        
        if any("CRITICAL" in x for x in result_list):
            [print(x) for x in result_list if re.search("CRITICAL", x)]
        if any("WARNING" in x for x in result_list):
            [print(x) for x in result_list if re.search("WARNING", x)]
        if any("OK -" in x for x in result_list):
            [print(x) for x in result_list if re.search("OK -", x)]
        
    
        if any("CRITICAL" in x for x in result_list):
            sys.exit(2)
        if any("WARNING" in x for x in result_list):
            sys.exit(1)
        
        sys.exit(0)
        


    def request(self, url, method='get', **kwargs):
            response = None
            try:
                if method == 'post':
                    response = requests.post(
                        url,
                        verify=not self.options.api_insecure,
                        data=kwargs.get('data', None),
                        timeout=5
                    )
                elif method == 'get':
                    response = requests.get(
                        url,
                        verify=not self.options.api_insecure,
                        cookies=self.__cookies,
                        headers=self.__headers,
                        params=kwargs.get('params', None),
                    )
                else:
                    self.output(CheckState.CRITICAL, "Unsupport request method: {}".format(method))
            except requests.exceptions.ConnectTimeout:
                self.output(CheckState.UNKNOWN, "Could not connect to PVE API: Connection timeout")
            except requests.exceptions.SSLError:
                self.output(CheckState.UNKNOWN, "Could not connect to PVE API: Certificate validation failed")
            except requests.exceptions.ConnectionError:
                self.output(CheckState.UNKNOWN, "Could not connect to PVE API: Failed to resolve hostname")

            if response.ok:
                return response.json()['data']
            else:
                message = "Could not fetch data from API: "

                if response.status_code == 401:
                    message += "Could not connection to PVE API: invalid username or password"
                elif response.status_code == 403:
                    message += "Access denied. Please check if API user has sufficient permissions / the role has been " \
                            "assigned."
                else:
                    message += "HTTP error code was {}".format(response.status_code)

                self.output(CheckState.UNKNOWN, message)



check_pve = CheckPVE()
check_pve.main()