from vpn_gate import vpn_manager
import logging

logging.basicConfig(level=logging.INFO)

def test_fetch():
    print("Fetching servers...")
    servers = vpn_manager.fetch_servers()
    print(f"Found {len(servers)} servers.")
    if servers:
        top = servers[0]
        print(f"Top server: {top['host']} in {top['country_long']}, score: {top['score']}, ping: {top['ping']}, speed: {top['speed']}")

if __name__ == "__main__":
    test_fetch()
