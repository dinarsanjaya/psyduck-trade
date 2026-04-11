"""
Proxy loader — reads from proxy.txt, random pick (sticky per session)
Format: http://user:pass@host:port (one per line)
"""
import random
import os

# No proxy — direct connections
def get_proxy():
    return None

def get_proxy_dict():
    return {}

def reset_proxy():
    pass
