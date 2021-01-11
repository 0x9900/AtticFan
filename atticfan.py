#
# (c) W6BSD Fred Cirera
# Check the file LICENCE on https://github.com/0x9900/AtticFan
#

import gc
import network
import os
import time
import uasyncio as asyncio
import ujson
import uselect as select
import usocket as socket

from machine import I2C
from machine import Pin
from machine import WDT
from machine import unique_id
from ubinascii import hexlify
from umqtt.robust import MQTTClient

import logging
import bme280

import wificonfig as wc

logging.basicConfig(level=logging.DEBUG)
LOG = logging.getLogger(wc.SNAME)

MQTT = False
SAMPLING = 120.0
STATE_FILE = "/tmp/state.json"
TEMPERATURE_THRESHOLD = 22.0

HTML_PATH = b'/html'

HTML_ERROR = """<!DOCTYPE html><html><head><title>404 Not Found</title>
<body><h1>{} {}</h1></body></html>
"""

HTTPCodes = {
  200: ('OK', 'OK'),
  303: ('Moved', 'Moved'),
  307: ('Temporary Redirect', 'Moved temporarily'),
  400: ('Bad Request', 'Bad request'),
  404: ('Not Found', 'File not found'),
  500: ('Internal Server Error', 'Server erro'),
}

MIME_TYPES = {
  b'css': 'text/css',
  b'html': 'text/html',
  b'js': 'application/javascript',
  b'json': 'application/json',
  b'txt': 'text/plain',
}

def parse_headers(head_lines):
  headers = {}
  for line in head_lines:
    if line.startswith(b'GET') or line.startswith(b'POST'):
      method, uri, proto = line.split()
      headers[b'Method'] = method
      headers[b'URI'] = uri
      headers[b'Protocol'] = proto
    else:
      try:
        key, val = line.split(b":", 1)
        headers[key] = val
      except:
        LOG.warning('header line warning: %s', line)
  return headers

class EnvSensor(bme280.BME280):

  _instance = None
  def __new__(cls, *args, **kwargs):
    if cls._instance is None:
      cls._instance = super(EnvSensor, cls).__new__(cls)
    return cls._instance

  def __init__(self, i2c=None):
    if hasattr(self, 'i2c'):
      return
    if not i2c:
      raise OSError('I2C bus argument missing')
    self.cache_time = 0
    super(EnvSensor, self).__init__(i2c=i2c)
    self.set_measurement_settings({
      'filter': bme280.BME280_FILTER_COEFF_16,
      'standby_time': bme280.BME280_STANDBY_TIME_500_US,
      'osr_h': bme280.BME280_OVERSAMPLING_1X,
      'osr_p': bme280.BME280_OVERSAMPLING_16X,
      'osr_t': bme280.BME280_OVERSAMPLING_2X})
    self.set_power_mode(bme280.BME280_NORMAL_MODE)

  def read_data(self):
    """Read the compensated data and cache it for 15 seconds"""
    now = time.time()
    if not hasattr(self, "compensated_data") or  now < self.cache_time + 30:
      self.cache_time = now
      self.compensated_data = self.get_measurement()

    return self.compensated_data

  @property
  def pressure(self):
    data = self.read_data()
    return data['pressure']

  @property
  def temp(self):
    data = self.read_data()
    return data['temperature']

  @property
  def humidity(self):
    data = self.read_data()
    return data['humidity']

  @property
  def temperature(self):
    return self.temp


class FAN:
  AUTOMATIC = const(2)
  ON = const(1)
  OFF = const(0)

  _instance = None
  def __new__(cls, *args, **kwargs):
    if cls._instance is None:
      cls._instance = super(FAN, cls).__new__(cls)
    return cls._instance

  def __init__(self, pin=None, sensor=None):
    if not hasattr(self, '_pin'):
      # This is the first call
      self._pin = pin
      self.sensor = sensor

    self._read_state()

  def _read_state(self):
    try:
      os.mkdir('/tmp')
      with open(STATE_FILE, "w") as fd:
        fd.write(ujson.dumps({"status": 2, "threshold": TEMPERATURE_THRESHOLD}))
    except OSError:
      pass

    try:
      state = {}
      with open(STATE_FILE, "r") as fd:
        state = ujson.loads(fd.read())
    except OSError as err:
      LOG.warning(err)

    self._status = state.get("status", 2)
    self._threshold = state.get("threshold", TEMPERATURE_THRESHOLD)
    gc.collect()

  def _save_state(self):
    try:
      with open(STATE_FILE, "w") as fd:
        fd.write(ujson.dumps({"status": self._status, "threshold": self._threshold}))
    except OSError as err:
      LOG.warning(err)

  @property
  def threshold(self):
    return self._threshold

  @threshold.setter
  def threshold(self, val):
    self._threshold = val
    self.runfan()
    self._save_state()

  def runfan(self):
    if self.threshold < self.sensor.temp:
      self.on()
    elif self.threshold > self.sensor.temp:
      self.off()

  async def run(self):
    while True:
      if self._status == self.AUTOMATIC:
        self.runfan()
      elif self._status == self.ON and not self.is_running():
        self.on()
      elif self._status == self.OFF and self.is_running():
        self.off()
      await asyncio.sleep_ms(500)

  def status(self, val=None):
    if val is None:
      return self._status
    try:
      val = int(val)
    except ValueError as err:
      LOG.error(err)
      return
    self._status = val
    self._save_state()

  def on(self):
    self._pin.on()

  def off(self):
    self._pin.off()

  def is_running(self):
    return bool(self._pin.value())

class Server:

  def __init__(self, addr='0.0.0.0', port=80):
    self.addr = addr
    self.port = port
    self.open_socks = []
    self.fan = FAN()
    self.sensor = EnvSensor()

  async def run(self, loop):
    addr = socket.getaddrinfo(self.addr, self.port, 0, socket.SOCK_STREAM)[0][-1]
    s_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s_sock.bind(addr)
    s_sock.listen(5)
    self.open_socks.append(s_sock)
    LOG.info('Awaiting connection on %s:%d', self.addr, self.port)

    poller = select.poll()
    poller.register(s_sock, select.POLLIN)
    while True:
      if poller.poll(1):  # 1ms
        c_sock, addr = s_sock.accept()  # get client socket
        LOG.info('Connection from %s:%d', *addr)
        loop.create_task(self.process_request(c_sock))
        gc.collect()
      await asyncio.sleep_ms(100)

  async def process_request(self, sock):
    LOG.info('Process request %s', sock)
    self.open_socks.append(sock)
    sreader = asyncio.StreamReader(sock)
    swriter = asyncio.StreamWriter(sock, '')
    try:
      head_lines = []
      while True:
        line = await sreader.readline()
        line = line.rstrip()
        if line in (b'', b'\r\n'):
          break
        head_lines.append(line)

      headers = parse_headers(head_lines)
      uri = headers.get(b'URI')
      if not uri:
        LOG.debug('Empty request')
        raise OSError

      LOG.info('Request %s %s', headers[b'Method'].decode(), uri.decode())
      if uri == b'/' or uri == b'/index.html':
        await self.send_file(swriter, b'/index.html')
      elif uri == b'/api/v1/sensors':
        data = await self.get_sensors()
        await self.send_json(swriter, data)
      elif uri == b'/api/v1/togglefan':
        self.fan.status((self.fan.status() + 1) % 3)
        data = await self.get_sensors()
        await self.send_json(swriter, data)
      elif uri.startswith('/api/v1/select/'):
        await self.switch_antenna(swriter, uri)
      elif 'threshold=' in uri:
        _, val = uri.split(b'=')
        if val.isdigit():
          self.fan.threshold = int(val)
          await self.send_redirect(swriter)
        else:
          await self.send_error(swriter, uri)
      else:
        await self.send_file(swriter, uri)
    except OSError:
      pass

    LOG.debug("%r", self.fan)
    gc.collect()
    LOG.debug('Disconnecting %s / %d', sock, len(self.open_socks))
    sock.close()
    self.open_socks.remove(sock)

  async def get_sensors(self):
    data = {}
    data['fan'] = self.fan.status()
    data['running'] = self.fan.is_running()
    data['threshold'] = self.fan.threshold
    data['temp'] = self.sensor.temp
    data['humidity'] = self.sensor.humidity
    data['pressure'] = self.sensor.pressure
    return data

  async def send_json(self, wfd, data):
    LOG.debug('send_json')
    jdata = ujson.dumps(data)
    await wfd.awrite(self._headers(200, b'json', content_len=len(jdata)))
    await wfd.awrite(jdata)
    gc.collect()

  async def send_file(self, wfd, url):
    LOG.debug('send_file: %s', url)
    fpath = b'/'.join([HTML_PATH, url.lstrip(b'/')])
    mime_type = fpath.split(b'.')[-1]

    try:
      with open(fpath, 'rb') as fd:
        await wfd.awrite(self._headers(200, mime_type, cache=-1))
        for line in fd:
          await wfd.awrite(line)
    except OSError as err:
      LOG.debug('send file error: %s %s', err, url)
      await self.send_error(wfd, 404)
    gc.collect()

  async def send_error(self, wfd, err_c):
    if err_c not in HTTPCodes:
      err_c = 400
    errors = HTTPCodes[err_c]
    await wfd.awrite(self._headers(err_c) + HTML_ERROR.format(err_c, errors[1]))
    gc.collect()

  async def send_redirect(self, wfd, location='/'):
    page = HTML_ERROR.format(303, 'redirect')
    await wfd.awrite(self._headers(303, location=location, content_len=len(page)))
    await wfd.awrite(HTML_ERROR.format(303, 'redirect'))
    gc.collect()

  def close(self):
    LOG.debug('Closing %d sockets', len(self.open_socks))
    for sock in self.open_socks:
      sock.close()

  @staticmethod
  def _headers(code, mime_type=None, location=None, content_len=0, cache=None):
    try:
      labels = HTTPCodes[code]
    except KeyError:
      raise KeyError('HTTP code (%d) not found', code)
    headers = []
    headers.append(b'HTTP/1.1 {:d} {}'.format(code, labels[0]))
    headers.append(b'Content-Type: {}'.format(MIME_TYPES.get(mime_type, 'text/html')))
    if location:
      headers.append(b'Location: {}'.format(location))
    if content_len:
      headers.append(b'Content-Length: {:d}'.format(content_len))

    if cache and cache == -1:
      headers.append(b'Cache-Control: public, max-age=604800, immutable')
    elif cache and isinstance(cache, str):
      headers.append(b'Cache-Control: '.format(cache))
    headers.append(b'Connection: close')

    return b'\n'.join(headers) + b'\n\n'


class MQTTData:

  def __init__(self, server, user, password, sname):
    self.topic = bytes('{}/feeds/{}-{{:s}}'.format(user, sname.lower()), 'utf-8').format

    client_id = hexlify(unique_id()).upper()
    self.client = MQTTClient(client_id, server, user=user, password=password)
    self.client.set_callback(self.buttons_cb)
    self.client.connect()
    # Subscribe to topics
    LOG.debug("Subscribe: %s", self.topic('force'))
    self.client.subscribe(self.topic('force'))

  def buttons_cb(self, topic, value):
    LOG.info('Button pressed: %s %s', topic.decode(), value.decode())
    if topic == self.topic('force') and value.upper() == b'TRUE':
      FAN().status(FAN.ON)
    elif topic == self.topic('force') and value.upper() == b'FALSE':
      FAN().status(FAN.AUTOMATIC)

  async def run(self):
    sensor = EnvSensor()
    if SAMPLING > 20:
      nb_samples = 7
      sampling = 1000 * (SAMPLING / nb_samples)
    else:
      nb_samples = 1
      sampling = SAMPLING
    sampling = int(sampling)

    while True:
      try:
        for key in ['temperature', 'pressure', 'humidity']:
          value = "{:.2f}".format(getattr(sensor, key))
          self.client.publish(self.topic(key), bytes(value, 'utf-8'))
          LOG.info('Publishing: %s: %s', key, value)
          await asyncio.sleep_ms(10)

        self.client.check_msg()

      except OSError as exc:
        LOG.error('MQTT %s %s', type(exc).__name__, exc)
        await asyncio.sleep_ms(750)
      finally:
        for _ in range(nb_samples):
          self.client.check_msg()
          await asyncio.sleep_ms(sampling)
      gc.collect()

def wifi_connect(ssid, password):
  ap_if = network.WLAN(network.AP_IF)
  ap_if.active(False)
  sta_if = network.WLAN(network.STA_IF)
  if not sta_if.isconnected():
    LOG.info('Connecting to WiFi...')
    sta_if.active(True)
    sta_if.connect(ssid, password)
    while not sta_if.isconnected():
      time.sleep(1)
  LOG.info('Network config: %s', sta_if.ifconfig())
  gc.collect()
  return sta_if

async def heartbeat():
  speed = 1500
  led = Pin(2, Pin.OUT, value=1)
  wdt = WDT()
  while True:
    led.value(led.value() ^ 1)
    wdt.feed()
    await asyncio.sleep_ms(speed)

def main():
  LOG.info('Last chance to press [^C]')
  time.sleep(3)
  i2c = I2C(-1, scl=Pin(5), sda=Pin(4))
  sensor = EnvSensor(i2c)
  fan = FAN(Pin(15, Pin.OUT, value=0), sensor)

  wifi = wifi_connect(wc.SSID, wc.PASSWORD)
  server = Server()

  loop = asyncio.get_event_loop()
  loop.create_task(heartbeat())
  loop.create_task(fan.run())
  loop.create_task(server.run(loop))
  if MQTT and wc.IO_USERNAME:
    mqtt = MQTTData(wc.IO_URL, wc.IO_USERNAME, wc.IO_KEY, wc.SNAME)
    loop.create_task(mqtt.run())

  try:
    loop.run_forever()
  except KeyboardInterrupt:
    LOG.info('Closing all connections')

if __name__ == "__main__":
    main()
