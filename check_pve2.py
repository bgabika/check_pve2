#!/usr/bin/python3
# -*- coding: utf-8 -*-
# ---------------------------------------------------------------
# COREX Proxmox VE check plugin for Icinga 2
# Copyright (C) 2019-2024  Gabor Borsos <bg@corex.bg>
# 
# v2.0 built on 2026.05.23.
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
# 2026.05.23. v2.0   - disk wearout is reversed to match proxmox UI and make more sense (0=no wearout, 100=full wearout)
#                    - fix output and add verbosity for disks_health output (include wearout details when OK)
#                    - add per-guest status checks for LXC and QEMU
#                    - add backup check for latest vzdump task status and backup age thresholds
# 2024.06.03. v1.25  - PVE8 - Ignore the syslog service based on the deprecation in Debian 12.5
# 2024.04.01. v1.24  - Add ceph-io subcommand
# 2022.12.13. v1.23  - Add help
# 2022.12.13. v1.22  - Bugfix, storage Graphite performance output
# 2022.12.08. v1.21  - Bugfix, storage size zero division
# 2022.12.06. v1.2  - Bugfix, storage check unit bug
# 2022.10.27. v1.1  - Bugfix, disk-health CRITICAL check
# 2022.10.23. v1.0  - First release
# ---------------------------------------------------------------

import re, sys, math

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
            {self.pluginname} --hostname pve.mydomain.com --api_user monitoring@pve --api_token A12fhaDFCjn92aKt=123f922a-e10b-12z7-e133-Aa3476b866ar --subcommand lxc --nodename pve1 --warning 80 --critical 90 --ignore-lxc-id 101 --ignore-lxc-id 108
            {self.pluginname} --hostname pve.mydomain.com --api_user monitoring@pve --api_token A12fhaDFCjn92aKt=123f922a-e10b-12z7-e133-Aa3476b866ar --subcommand qemu --nodename pve1 --warning 80 --critical 90 --ignore-qemu-id 200
            {self.pluginname} --hostname pve.mydomain.com --api_user monitoring@pve --api_token A12fhaDFCjn92aKt=123f922a-e10b-12z7-e133-Aa3476b866ar --subcommand backup --nodename pve1 --warning 7 --critical 14
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


        check_pve_opt = parser.add_argument_group('check arguments', 'backup, ceph, ceph_io, cluster, cpu, disks_health, lxc, memory, pveversion, qemu, services, storage, swap')
        
        check_pve_opt.add_argument("--subcommand",
                                        choices=(
                                            'backup', 'ceph', 'ceph_io', 'cluster', 'cpu', 'disks_health', 'lxc', 'memory', 'pveversion', 'qemu', 'services', 'storage', 'swap'),
                                        required=True,
                                        help="Select subcommand to use. cpu, memory, swap, storage, disks_health, lxc, qemu and backup need warning and critical thresholds.")
        
        check_pve_opt.add_argument('--nodename', type=str, required=True, help="node name")
        
        check_pve_opt.add_argument('--ignore-disk', dest='ignore_disks', action='append', metavar='DISKNAME',
                                        help='Ignore disks in health check, --ignore-disk disk1 --ignore-disk disk2 ...etc', default=[])

        check_pve_opt.add_argument('--ignore-service', dest='ignore_services', action='append', metavar='SVCNAME',
                                        help='Ignore services in services health check, --ignore-service corosync --ignore-service xyz ...etc', default=[])

        check_pve_opt.add_argument('--ignore-lxc-id', dest='ignore_lxc_ids', action='append', metavar='VMID', type=int,
                        help='Ignore LXC by VMID in lxc check, --ignore-lxc-id 101 --ignore-lxc-id 102 ...etc', default=[])

        check_pve_opt.add_argument('--ignore-qemu-id', dest='ignore_qemu_ids', action='append', metavar='VMID', type=int,
                        help='Ignore QEMU by VMID in qemu check, --ignore-qemu-id 201 --ignore-qemu-id 202 ...etc', default=[])

        check_pve_opt.add_argument('--disk-name', dest='include_disks', action='append', metavar='DISKNAME',
                                        help='Check disks in health check by disk name, --disk-name disk1 --disk-name disk2 ...etc', default=[])
        
        check_pve_opt.add_argument('--ceph-io-warning', dest='ceph_io_warning', type=int,
                                help='IO read/write warning threshold for ceph-io checking. Default: 10000 operations/sec', default=10000)
        
        check_pve_opt.add_argument('--ceph-byte-warning', dest='ceph_byte_warning', type=int,
                                help='Byte read/write warning threshold for ceph-io checking. Default: 200MB/sec', default=200)

        check_pve_opt.add_argument('--warning', dest='threshold_warning', type=int,
                                help='Warning threshold for cpu, memory, swap, storage, disks_health, lxc, qemu and backup checks (percent or days)')
        
        check_pve_opt.add_argument('--critical', dest='threshold_critical', type=int,
                                help='Critical threshold for cpu, memory, swap, storage, disks_health, lxc, qemu and backup checks (percent or days)')

        self.options = parser.parse_args()

        if (self.options.subcommand == "backup" or self.options.subcommand == "cpu" or self.options.subcommand == "disks_health" or \
            self.options.subcommand == "lxc" or self.options.subcommand == "memory" or self.options.subcommand == "qemu" or \
            self.options.subcommand == "storage" or self.options.subcommand == "swap") and \
            (self.options.threshold_warning is None or self.options.threshold_critical is None):
            
            parser.error(f"--warning and --critical arguments are required for '{self.options.subcommand}' subcommand!")
            
        if self.check_thresholds_scale() == False:
            parser.error(f"--warning threshold must be lower then --critical threshold for '{self.options.subcommand}' subcommand!")


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
        elif apiurl == "backup":
            return self.API_URL.format(hostname=self.options.api_host, port=self.options.api_port, command=f"nodes/{self.options.nodename}/tasks")
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
        elif apiurl == "lxc":
            return self.API_URL.format(hostname=self.options.api_host, port=self.options.api_port, command=f"nodes/{self.options.nodename}/lxc")
        elif apiurl == "qemu":
            return self.API_URL.format(hostname=self.options.api_host, port=self.options.api_port, command=f"nodes/{self.options.nodename}/qemu")
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


    def check_thresholds_scale(self):
        if (self.options.subcommand == "backup" or self.options.subcommand == "cpu" or self.options.subcommand == "disks_health" or \
            self.options.subcommand == "lxc" or self.options.subcommand == "memory" or self.options.subcommand == "qemu" or \
            self.options.subcommand == "storage" or self.options.subcommand == "swap"):
            return(self.options.threshold_warning < self.options.threshold_critical)


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


    def check_backup(self, request_output, subcommand):
        backup_tasks = [task for task in request_output if task.get("type") == "vzdump"]

        if len(backup_tasks) == 0:
            self.output(CheckState.WARNING, "No backup tasks found for this node.")

        def task_started_at(task):
            try:
                return int(task.get("starttime", 0))
            except (TypeError, ValueError):
                return 0

        latest_task = max(backup_tasks, key=task_started_at)
        successful_tasks = [task for task in backup_tasks if str(task.get("status", "")).upper() == "OK"]
        latest_successful_task = max(successful_tasks, key=task_started_at) if len(successful_tasks) > 0 else None

        latest_task_status = str(latest_task.get("status", "unknown")).upper()
        latest_task_text = latest_task.get("id", latest_task.get("upid", "unknown backup task"))
        message_parts = [f"Latest backup task {latest_task_text} ended with status {latest_task_status}"]

        if latest_task_status != "OK":
            failure_state = CheckState.WARNING
            message_parts.append("last backup failed")
        else:
            failure_state = CheckState.OK
            message_parts.append("last backup succeeded")

        if latest_successful_task is None:
            age_state = CheckState.WARNING
            age_days = None
            message_parts.append("no successful backup was found")
        else:
            try:
                latest_successful_start = int(latest_successful_task.get("starttime", 0))
            except (TypeError, ValueError):
                latest_successful_start = 0

            age_days = round((datetime.now() - datetime.fromtimestamp(latest_successful_start)).total_seconds() / 86400, 2)
            if age_days >= self.options.threshold_critical:
                age_state = CheckState.CRITICAL
            elif age_days >= self.options.threshold_warning:
                age_state = CheckState.WARNING
            else:
                age_state = CheckState.OK

            message_parts.append(f"last successful backup is {age_days} days old")

        final_state = failure_state
        if age_state.value > final_state.value:
            final_state = age_state

        perfdata_age = age_days if age_days is not None else "U"
        perfdata = f"|'backup_age_days'={perfdata_age};{self.options.threshold_warning};{self.options.threshold_critical};0;"
        self.result_list.append(f"{final_state.name} - {'; '.join(message_parts)}. {perfdata}")
        


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
            disk_serial = disk["serial"]
            disk_model = disk["model"]
            disk_type = disk["type"]
            disk_devpath = disk["devpath"]
            disk_health = disk["health"]
            disk_wearout_raw = disk.get("wearout")
            disk_wearout_value = None

            try:
                parsed_wearout = float(disk_wearout_raw)
                if math.isfinite(parsed_wearout):
                    disk_wearout_value = parsed_wearout
            except (TypeError, ValueError):
                pass

            if disk_wearout_value is None:
                # disk wearout value if not available - set it to 0 to avoid false warning/critical alerts
                disk_wearout = 0
            else:
                # in API we got 0 for maximum wearout and 100 for none; reverse it to match proxmox UI and to make more sense
                disk_wearout = round(100 - disk_wearout_value, 2)

            disk_name_with_details = f"{disk_model} ({disk_type}, SN: {disk_serial}) on {disk_devpath}"
            disk_perf_name = re.sub(r"[^A-Za-z0-9_]+", "_", f"disk_{disk_devpath}").strip("_").lower()
            perfdata = f"|'{disk_perf_name}_wearout'={disk_wearout}%;{self.options.threshold_warning};{self.options.threshold_critical};0;100"

            if disk_health != "OK" and disk_health != "PASSED" and disk_health != "UNKNOWN":
                self.result_list.append(f"CRITICAL - {disk_name_with_details} has failed: {disk_health}. {perfdata}")
            elif isinstance(disk_wearout, (int, float)) and disk_wearout >= self.options.threshold_critical:
                self.result_list.append(f"CRITICAL - {disk_name_with_details} has high wearout {disk_wearout}%. {perfdata}")
            elif isinstance(disk_wearout, (int, float)) and disk_wearout >= self.options.threshold_warning:
                self.result_list.append(f"WARNING - {disk_name_with_details} has high wearout {disk_wearout}%. {perfdata}")
            else:
                self.result_list.append(f"OK - {disk_name_with_details} has low wearout {disk_wearout}%. {perfdata}")
	
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
            service_name_with_details = f"{service_name} in state {service_state} ({service_unit_state}, {service_active_state})"

            if service_name in self.options.ignore_services:
                self.result_list.append(f"OK - {service_name_with_details}. State is ignored.")
            elif service_unit_state == "not-found":
                self.result_list.append(f"OK - {service_name_with_details}. Ignored as in state not-found.")
            elif service_state == "running" and service_active_state == "active":
                self.result_list.append(f"OK - {service_name_with_details}.")
            else:
                self.result_list.append(f"WARNING - {service_name_with_details}.")

        self.result_list = set(self.result_list)


    def check_lxc(self, request_output, subcommand):
        self.check_guests(request_output, "lxc", self.options.ignore_lxc_ids)


    def check_qemu(self, request_output, subcommand):
        self.check_guests(request_output, "qemu", self.options.ignore_qemu_ids)


    def check_guests(self, request_output, guest_type, ignore_vmid_list):
        for guest in request_output:
            vmid = guest.get("vmid", "unknown")
            vmid_int = None
            try:
                vmid_int = int(vmid)
            except (TypeError, ValueError):
                vmid_int = None

            guest_name = guest.get("name", f"{guest_type}-{vmid}")
            guest_status = guest.get("status", guest.get("qmpstatus", "unknown"))
            guest_cpus = guest.get("cpus", 0)
            guest_uptime = guest.get("uptime", 0)

            if vmid_int is not None and vmid_int in ignore_vmid_list:
                self.result_list.append(f"OK - {guest_type.upper()} {guest_name} (vmid: {vmid}) is ignored.")
                continue

            cpu_raw = guest.get("cpu", 0)
            cpu_percent = round((cpu_raw * 100), 2)

            mem_used = guest.get("mem", 0)
            mem_total = guest.get("maxmem", 0)
            swap_used = guest.get("swap", 0)
            swap_total = guest.get("maxswap", 0)
            disk_used = guest.get("disk", 0)
            disk_total = guest.get("maxdisk", 0)

            mem_percent = round((mem_used / mem_total) * 100, 2) if mem_total else 0
            swap_percent = round((swap_used / swap_total) * 100, 2) if swap_total else 0
            disk_percent = round((disk_used / disk_total) * 100, 2) if disk_total else 0

            guest_label = f"{guest_type}_{vmid}"
            perfdata = f"|'{guest_label}_cpu'={cpu_percent}%;0;100;0;100 " \
                       f"'{guest_label}_mem'={mem_percent}%;{self.options.threshold_warning};{self.options.threshold_critical};0;100 " \
                       f"'{guest_label}_swap'={swap_percent}%;{self.options.threshold_warning};{self.options.threshold_critical};0;100 " \
                       f"'{guest_label}_disk'={disk_percent}%;{self.options.threshold_warning};{self.options.threshold_critical};0;100"

            base_message = f"{guest_type.upper()} {guest_name} (vmid: {vmid}) is {guest_status} " \
                           f"(cpu: {cpu_percent}%, mem: {mem_percent}%, swap: {swap_percent}%, disk: {disk_percent}%, cpus: {guest_cpus}, uptime: {guest_uptime}s)"

            threshold_values = {
                "memory": mem_percent,
                "swap": swap_percent,
                "disk": disk_percent,
            }
            critical_metrics = [name for name, value in threshold_values.items() if value >= self.options.threshold_critical]
            warning_metrics = [name for name, value in threshold_values.items() if self.options.threshold_warning <= value < self.options.threshold_critical]

            if guest_status != "running":
                self.result_list.append(f"CRITICAL - {base_message}. {perfdata}")
            elif len(critical_metrics) > 0:
                self.result_list.append(f"CRITICAL - {base_message}; exceeded critical threshold on: {', '.join(critical_metrics)}. {perfdata}")
            elif len(warning_metrics) > 0:
                self.result_list.append(f"WARNING - {base_message}; exceeded warning threshold on: {', '.join(warning_metrics)}. {perfdata}")
            else:
                self.result_list.append(f"OK - {base_message}. {perfdata}")

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
