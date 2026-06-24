import requests
import base64
import subprocess
import os
import time
import threading
import logging
import random

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

VPN_GATE_API_URL = "https://www.vpngate.net/api/iphone/"
OVPN_CONFIG_PATH = "/tmp/vpngate.ovpn"
VPN_PROCESS = None
VPN_LOCK = threading.Lock()

class VPNManager:
    def __init__(self):
        self.process = None
        self.is_connected = False
        self.current_server = None
        self.auto_switch_thread = None
        self.stop_event = threading.Event()

    def fetch_servers(self):
        try:
            response = requests.get(VPN_GATE_API_URL, timeout=10)
            if response.status_code != 200:
                logger.error(f"Failed to fetch VPN Gate servers: {response.status_code}")
                return []
            
            content = response.text
            lines = content.splitlines()
            if len(lines) < 2:
                return []
            
            header = lines[1].split(',')
            servers = []
            for line in lines[2:]:
                if line.startswith('*') or not line.strip():
                    continue
                parts = line.split(',')
                if len(parts) < 15:
                    continue
                
                server = {
                    'host': parts[0],
                    'ip': parts[1],
                    'score': int(parts[2]) if parts[2].isdigit() else 0,
                    'ping': int(parts[3]) if parts[3].isdigit() else 999,
                    'speed': int(parts[4]) if parts[4].isdigit() else 0,
                    'country_long': parts[5],
                    'country_short': parts[6],
                    'num_vpn_sessions': int(parts[7]) if parts[7].isdigit() else 0,
                    'uptime': int(parts[8]) if parts[8].isdigit() else 0,
                    'total_users': int(parts[9]) if parts[9].isdigit() else 0,
                    'total_traffic': parts[10],
                    'log_type': parts[11],
                    'operator': parts[12],
                    'message': parts[13],
                    'ovpn_config_base64': parts[14]
                }
                servers.append(server)
            
            # Sort by score descending and ping ascending
            servers.sort(key=lambda x: (-x['score'], x['ping']))
            return servers
        except Exception as e:
            logger.error(f"Error fetching VPN servers: {e}")
            return []

    def connect(self, server=None):
        with VPN_LOCK:
            self.disconnect()
            
            if not server:
                servers = self.fetch_servers()
                if not servers:
                    logger.error("No VPN servers available.")
                    return False
                # Pick one of the top 5 servers randomly for variety
                server = random.choice(servers[:5])
            
            self.current_server = server
            try:
                ovpn_config = base64.b64decode(server['ovpn_config_base64']).decode('utf-8')
                with open(OVPN_CONFIG_PATH, 'w') as f:
                    f.write(ovpn_config)
                
                # Command to start openvpn
                # Using --daemon can make it hard to track, so we'll run it and track the process
                cmd = ['sudo', 'openvpn', '--config', OVPN_CONFIG_PATH, '--dev', 'tun0']
                self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                
                # Wait for initialization sequence completed
                start_time = time.time()
                while time.time() - start_time < 30:
                    line = self.process.stdout.readline()
                    if "Initialization Sequence Completed" in line:
                        logger.info(f"Connected to VPN: {server['host']} ({server['country_long']})")
                        self.is_connected = True
                        return True
                    if self.process.poll() is not None:
                        stderr = self.process.stderr.read()
                        logger.error(f"OpenVPN exited early: {stderr}")
                        break
                    time.sleep(0.1)
                
                self.disconnect()
                return False
            except Exception as e:
                logger.error(f"Failed to connect to VPN: {e}")
                self.disconnect()
                return False

    def disconnect(self):
        if self.process:
            logger.info("Disconnecting VPN...")
            # Kill the process group to ensure all child processes are gone
            subprocess.run(['sudo', 'killall', 'openvpn'], capture_output=True)
            self.process.terminate()
            self.process.wait()
            self.process = None
        self.is_connected = False
        self.current_server = None
        if os.path.exists(OVPN_CONFIG_PATH):
            os.remove(OVPN_CONFIG_PATH)

    def monitor_speed_and_auto_switch(self):
        """Monitor speed and switch if it drops significantly."""
        while not self.stop_event.is_set():
            if self.is_connected:
                # Simple check: ping a reliable host
                try:
                    start = time.time()
                    requests.get("https://www.google.com", timeout=5)
                    latency = (time.time() - start) * 1000
                    logger.info(f"Current VPN Latency: {latency:.2f}ms")
                    
                    if latency > 2000: # If latency is over 2s, it's slow
                        logger.warning("VPN speed slow, switching...")
                        self.connect()
                except Exception:
                    logger.warning("VPN connection lost or very slow, switching...")
                    self.connect()
            
            time.sleep(60) # Check every minute

    def start_auto_switch(self):
        if not self.auto_switch_thread or not self.auto_switch_thread.is_alive():
            self.stop_event.clear()
            self.auto_switch_thread = threading.Thread(target=self.monitor_speed_and_auto_switch, daemon=True)
            self.auto_switch_thread.start()

    def stop_auto_switch(self):
        self.stop_event.set()

vpn_manager = VPNManager()
