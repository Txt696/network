"""
Network tools module for NetMaster.
Provides ping, traceroute, port scanner, subnet calculator and more.

For Ali - Network Engineer
"""

import subprocess
import socket
import ipaddress
import threading
import re
from typing import List, Dict, Optional


class PingTool:
    """Ping utility for network connectivity testing."""
    
    def __init__(self):
        self.results = []
    
    def ping(self, host: str, count: int = 4, timeout: int = 2) -> Dict:
        """
        Ping a host and return results.
        
        Args:
            host: Hostname or IP address
            count: Number of packets to send
            timeout: Timeout in seconds
            
        Returns:
            Dictionary with ping statistics
        """
        try:
            # Determine OS and build command
            import platform
            system = platform.system()
            
            if system == 'Windows':
                cmd = ['ping', '-n', str(count), '-w', str(timeout * 1000), host]
            else:
                cmd = ['ping', '-c', str(count), '-W', str(timeout), host]
            
            # Execute ping
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=count * timeout + 5
            )
            
            # Parse output
            output = result.stdout + result.stderr
            stats = self._parse_ping_output(output, system)
            
            return {
                'success': result.returncode == 0,
                'output': output,
                'stats': stats
            }
            
        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'output': 'Ping timed out',
                'stats': {}
            }
        except Exception as e:
            return {
                'success': False,
                'output': f'Error: {str(e)}',
                'stats': {}
            }
    
    def _parse_ping_output(self, output: str, system: str) -> Dict:
        """Parse ping command output."""
        stats = {}
        
        if system == 'Windows':
            # Windows parsing
            lost_match = re.search(r'\((\d+)% loss\)', output)
            if lost_match:
                stats['loss'] = int(lost_match.group(1))
            
            time_matches = re.findall(r'time[=<](\d+)ms', output)
            if time_matches:
                times = [int(t) for t in time_matches]
                stats['min'] = min(times)
                stats['max'] = max(times)
                stats['avg'] = sum(times) // len(times)
        else:
            # Linux/Unix parsing
            lost_match = re.search(r'(\d+)% packet loss', output)
            if lost_match:
                stats['loss'] = int(lost_match.group(1))
            
            rtt_match = re.search(r'rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)', output)
            if rtt_match:
                stats['min'] = float(rtt_match.group(1))
                stats['avg'] = float(rtt_match.group(2))
                stats['max'] = float(rtt_match.group(3))
        
        return stats


class TracerouteTool:
    """Traceroute utility for path discovery."""
    
    def trace(self, host: str, max_hops: int = 30) -> Dict:
        """
        Perform traceroute to host.
        
        Args:
            host: Destination hostname or IP
            max_hops: Maximum number of hops
            
        Returns:
            Dictionary with hop information
        """
        try:
            import platform
            system = platform.system()
            
            if system == 'Windows':
                cmd = ['tracert', '-h', str(max_hops), '-d', host]
            else:
                cmd = ['traceroute', '-m', str(max_hops), '-n', host]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=max_hops * 3 + 10
            )
            
            hops = self._parse_traceroute_output(result.stdout, system)
            
            return {
                'success': True,
                'output': result.stdout,
                'hops': hops
            }
            
        except Exception as e:
            return {
                'success': False,
                'output': f'Error: {str(e)}',
                'hops': []
            }
    
    def _parse_traceroute_output(self, output: str, system: str) -> List[Dict]:
        """Parse traceroute output into structured data."""
        hops = []
        lines = output.strip().split('\n')
        
        for line in lines[1:]:  # Skip header
            if not line.strip():
                continue
            
            # Simple parsing - can be enhanced
            parts = line.split()
            if parts and parts[0].isdigit():
                hop_info = {
                    'hop': int(parts[0]),
                    'raw': line
                }
                
                # Try to extract IP
                for part in parts:
                    if re.match(r'\d+\.\d+\.\d+\.\d+', part):
                        hop_info['ip'] = part
                        break
                
                hops.append(hop_info)
        
        return hops


class PortScanner:
    """Simple port scanner for network reconnaissance."""
    
    def scan(self, host: str, ports: List[int] = None, 
             timeout: float = 1.0) -> Dict:
        """
        Scan ports on a host.
        
        Args:
            host: Target hostname or IP
            ports: List of ports to scan (default: common ports)
            timeout: Connection timeout in seconds
            
        Returns:
            Dictionary with open/closed ports
        """
        if ports is None:
            # Common network service ports
            ports = [21, 22, 23, 25, 53, 80, 110, 143, 443, 993, 995, 
                    3306, 3389, 5432, 8080, 8443]
        
        results = {
            'open': [],
            'closed': [],
            'filtered': []
        }
        
        def check_port(port):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                result = sock.connect_ex((host, port))
                sock.close()
                
                if result == 0:
                    results['open'].append(port)
                else:
                    results['closed'].append(port)
            except socket.timeout:
                results['filtered'].append(port)
            except Exception:
                results['filtered'].append(port)
        
        # Run scans in threads for speed
        threads = []
        for port in ports:
            t = threading.Thread(target=check_port, args=(port,))
            t.start()
            threads.append(t)
        
        for t in threads:
            t.join()
        
        return {
            'success': True,
            'host': host,
            'ports_scanned': len(ports),
            'results': results
        }


class SubnetCalculator:
    """Subnet calculator for network planning."""
    
    def calculate(self, network: str) -> Dict:
        """
        Calculate subnet information.
        
        Args:
            network: Network in CIDR notation (e.g., '192.168.1.0/24')
            
        Returns:
            Dictionary with subnet details
        """
        try:
            net = ipaddress.ip_network(network, strict=False)
            
            return {
                'success': True,
                'network': str(net),
                'netmask': str(net.netmask),
                'wildcard': str(self._wildcard_mask(net.netmask)),
                'first_host': str(net.network_address + 1) if net.num_addresses > 2 else str(net.network_address),
                'last_host': str(net.broadcast_address - 1) if net.num_addresses > 2 else str(net.broadcast_address),
                'broadcast': str(net.broadcast_address),
                'total_hosts': net.num_addresses,
                'usable_hosts': max(0, net.num_addresses - 2),
                'binary_netmask': bin(int(net.netmask))[2:],
                'hosts_per_byte': self._hosts_per_octet(net)
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _wildcard_mask(self, netmask) -> ipaddress.IPv4Address:
        """Calculate wildcard mask from netmask."""
        netmask_int = int(netmask)
        wildcard_int = 0xFFFFFFFF ^ netmask_int
        return ipaddress.IPv4Address(wildcard_int)
    
    def _hosts_per_octet(self, net) -> List[int]:
        """Calculate hosts per octet."""
        # Simplified calculation
        return [0, 0, 0, 0]


class DNSLookup:
    """DNS lookup utility."""
    
    def lookup(self, hostname: str, record_type: str = 'A') -> Dict:
        """
        Perform DNS lookup.
        
        Args:
            hostname: Domain name to lookup
            record_type: DNS record type (A, AAAA, MX, NS, etc.)
            
        Returns:
            Dictionary with DNS records
        """
        try:
            # Using socket for basic A record lookup
            if record_type == 'A':
                result = socket.gethostbyname_ex(hostname)
                return {
                    'success': True,
                    'hostname': hostname,
                    'addresses': result[2],
                    'aliases': result[1]
                }
            else:
                # For other record types, would need dnspython library
                return {
                    'success': False,
                    'error': f'Record type {record_type} requires dnspython library'
                }
                
        except socket.gaierror as e:
            return {
                'success': False,
                'error': f'DNS lookup failed: {str(e)}'
            }


# Convenience functions for quick access
def ping(host, count=4):
    """Quick ping function."""
    return PingTool().ping(host, count)


def scan_ports(host, ports=None):
    """Quick port scan function."""
    return PortScanner().scan(host, ports)


def calc_subnet(network):
    """Quick subnet calculation."""
    return SubnetCalculator().calculate(network)
