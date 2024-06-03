
# check_pve2

COREX PROXMOX VE check plugin for Icinga 2
 
### Features
 - checks PROXMOX VE host over API
 - authentication with api key and password
 - prints performance data for Icinga 2 Graphite Module ( and other solutions like Graphite )
 - available subcommands: ceph, cluster, cpu, disks_health, memory, pveversion, services, storage, swap
 - warning/critical thresholds for each separate subcommands
 - for more details run check_pve2.py --help

### Usage

<pre><code>
# cd /usr/lib/nagios/plugins
# ./check_pve2.py --hostname pve.mydomain.com --api_user monitoring@pve --api_token A12fhaDFCjn92aKt=123f922a-e10b-12z7-e133-Aa3476b866ar --subcommand cpu --nodename pve --warning 65 --critical 85
OK - CPU usage is 25.07 %. |usage=25.07%;65;85;0;100

</code></pre>

<pre><code>
# cd /usr/lib/nagios/plugins
# ./check_pve2.py --hostname pve.mydomain.com --api_user monitoring@pve --api_token A12fhaDFCjn92aKt=123f922a-e10b-12z7-e133-Aa3476b866ar --subcommand storage --nodename pve --warning 70 --critical 80 --ignore-disk vm-backup
OK - pbs-backup disk usage (type: pbs) is 66.38 % (5.7 TB / 8.59 TB).                        |pbs-backup=5.7TB;6.01;6.87;0;8.59
OK - local disk usage (type: dir) is 1.57 % (6.75 GB / 430.19 GB).                        |local=6.75GB;301.13;344.15;0;430.19
OK - ceph_hdd disk usage (type: rbd) is 56.5 % (11.7 TB / 20.71 TB).                        |ceph_hdd=11.7TB;14.5;16.57;0;20.71
OK - local-zfs disk usage (type: zfspool) is 0.0 % (0.09 MB / 423.44 GB).                        |local-zfs=0.0GB;296.41;338.75;0;423.44
OK - ceph_ssd disk usage (type: rbd) is 62.29 % (5.88 TB / 9.44 TB).                        |ceph_ssd=5.88TB;6.61;7.55;0;9.44

</code></pre>


<pre><code>
# cd /usr/lib/nagios/plugins
# ./check_pve2.py --hostname pve.mydomain.com --api_user monitoring@pve --api_password nagios --subcommand cluster
OK - corexcluster cluster is working well.

</code></pre>

### Version

 - 1.25

### ToDo

 - waiting for bugs or feature requests (-:

### Changelog
 
- 2024.06.03. v1.25  - PVE8 - Ignore the syslog service based on the deprecation in Debian 12.5
- 2024.04.01. v1.24  - Add ceph-io subcommand
- 2022.12.13. v1.23  - Add help
- 2022.12.13. v1.22  - Bugfix, storage Graphite performance output
- 2022.12.08. v1.21  - Bugfix, storage size zero division
- 2022.12.06. v1.2  - Bugfix, storage check unit bug
- 2022.10.27. v1.1  - Bugfix, disk-health CRITICAL check
- 2022.10.23. v1.0  - First release

