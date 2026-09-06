#!/usr/bin/env python3
"""Is the self-hosted Photon Server actually reachable from this network?

  python3 tools/photon_probe.py 192.168.137.1

Run it from a laptop joined to the same hotspot before blaming the game.
Photon's UDP game protocol does not answer unsolicited packets, so the check
is TCP-based: the LoadBalancing app listens on 4530/4531 (and 9090 for
websockets) whenever the UDP listeners are up too.
"""
import argparse
import socket
import sys

TCP_PORTS = [(4530, "Master (TCP)"), (4531, "Game (TCP)"), (9090, "WebSocket")]
UDP_PORTS = [(5055, "Master (UDP) -- what the patched game connects to"), (5056, "Game (UDP)")]


def tcp_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False


def udp_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """True unless the host actively refuses the datagram.

    Photon does not answer unsolicited packets, so silence is the good case;
    a closed port answers ICMP unreachable, which Linux surfaces as
    ConnectionRefusedError on the next recv of a connected UDP socket.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        s.send(b"\x00")
        s.recv(1)
        return True
    except (socket.timeout, TimeoutError):
        return True
    except OSError:
        return False
    finally:
        s.close()


def local_addresses() -> list:
    out = []
    for family, kind in ((socket.AF_INET, "8.8.8.8"),):
        s = socket.socket(family, socket.SOCK_DGRAM)
        try:
            s.connect((kind, 80))
            out.append(s.getsockname()[0])
        except OSError:
            pass
        finally:
            s.close()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("--timeout", type=float, default=1.5)
    a = ap.parse_args(argv)

    print(f"this machine: {', '.join(local_addresses()) or 'unknown'}")
    reachable = False
    for port, label in TCP_PORTS:
        ok = tcp_open(a.host, port, a.timeout)
        reachable |= ok
        print(f"  {a.host}:{port:<5} {'OPEN' if ok else 'closed':7} {label}")
    for port, label in UDP_PORTS:
        ok = udp_open(a.host, port, a.timeout)
        print(f"  {a.host}:{port:<5} {'no reply' if ok else 'REFUSED':7} {label}")
    if not reachable:
        print("\nNothing answered. Check: Photon Server running, Windows Firewall allows "
              "PhotonSocketServer, and the phone is on the same hotspot/Wi-Fi.")
    return 0 if reachable else 1


if __name__ == "__main__":
    sys.exit(main())
