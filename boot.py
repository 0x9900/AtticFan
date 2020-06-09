#
# Verify if some packages are installed. Install the packages if they are missing and then reboot.
# So far the only required package is logging.

import time
import gc

def temporize(ll=10):
  for _ in range(ll):
    print('.', end='')
    time.sleep(1)
  print('')

def do_connect():
  import wificonfig as wc
  import network
  sta_if = network.WLAN(network.STA_IF)
  if not sta_if.isconnected():
    print('Connecting to network...')
    sta_if.active(True)
    sta_if.connect(wc.SSID, wc.PASSWORD)
    while not sta_if.isconnected():
      time.sleep(1)
  print('Network config:', sta_if.ifconfig())

try:
  import logging
except ImportError:
  temporize()
  do_connect()
  from machine import reset
  from upip import install
  install('logging')
  temporize(3)
  reset()

def no_debug():
  import esp
  # this can be run from the REPL as well
  esp.osdebug(None)

gc.collect()
