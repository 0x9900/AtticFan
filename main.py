
import dht
import gc
import logging
import machine
import network
import ntptime
import time
import uasyncio as asyncio
import ujson
import uselect as select
import usocket as socket

from machine import Pin

logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger("ESP32")

OFF = 0
ON = 1

FAN = Pin(12, Pin.OUT)
FAN_FORCE = False

DHT_GPIO = 13

TEMPERATURE_THRESHOLD = 24.0

HTTP_ERR = """
<html>
<head>
 <title>%d %s</title>
 <meta name="viewport" content="width=device-width, initial-scale=1">
 <meta http-equiv="Refresh" content="5; URL=/">
 <link rel="icon" href="data:,">
</head>
<body>
<H2>%d (%s).</h2>
</body>
</html>
"""

TEMPLATE =  """
<html>
<head>
 <title>Attic Fan</title>
 <meta name="viewport" content="width=device-width, initial-scale=1">
 <link rel="icon" href="data:,">
 <style>
    html{font-family: Helvetica; display:inline-block; margin: 0px auto; text-align: center;}
    h1{color: #0F3376; padding: 2vh;}
    a{text-decoration: none;}
    p{font-size: 1.2em;}
    p.tpx{font-size: 1em; color: #FF8800;}
    .button{display: inline-block; background-color: #e7bd3b; border: none;border-radius: 4px; color: white;
            padding: 12px 20px; text-decoration: none; font-size: 18px; margin: 2px; cursor: pointer;}
    .button2{background-color: #4286f4;}
  </style>
</head>
<body>
  <a href="/"><h1>Attic Fan</h1></a>
  <p class="tpx">Temperature: <strong>%0.2f</strong>C / Humidity: <strong>%0.2f</strong>%%</p>
  <hr>
  <p>Fan status: <strong>%s</strong> / Fan: <strong>%s</strong></p>
  <p>
    <a href="/?force=on"><button class="button">Force</button></a>
    <a href="/?force=off"><button class="button button2">Automatic</button></a>
  </p>
  <hr>
  <p><form>
      <label for="cars">Temperature threshold:</label>
      <select name="temp" id="temp" onchange="this.form.submit()">
	<option value="17">17</option>
	<option value="18">18</option>
	<option value="19">19</option>
	<option value="20">20</option>
	<option value="21">21</option>
	<option value="22">22</option>
	<option value="23">23</option>
	<option value="24">24</option>
	<option value="25">25</option>
	<option value="26">26</option>
	<option value="27">27</option>
	<option value="28">28</option>
      </select> C
    </form>
  </p>
  <script>
    document.getElementById("temp").value = %d;
  </script>
</body>
</html>
"""


class RHSensor:

  def __init__(self, gpio):
    self.sensor = dht.DHT22(Pin(gpio))
    self.next_read = 0
    self._temperature = 0
    self._humidity = 0
    try:
      self.sensor.measure()
    except:
      pass

  def reset(self):
    self.next_read = 0

  def read(self):
    now = time.time()
    if now <= self.next_read:
      return
    LOG.debug('RHSensor: read')
    sensor = self.sensor
    try:
      sensor.measure()
    except OSError as err:
      LOG.error('RHSensor Error: %s', err)
      self.err = True
      raise

    self.next_read = now + 60
    self._temperature = round(sensor.temperature(), 2)
    self._humidity = round(sensor.humidity(), 2)
    self.time = time.time()

  @property
  def temperature(self):
    try:
      self.read()
    except OSError:
      LOG.warning('Unreliable temperature')
    return self._temperature

  @property
  def humidity(self):
    try:
      self.read()
    except OSError:
      LOG.warning('Unreliable humidity')
    return self._humidity


class HTTPServer:
  def __init__(self, addr='0.0.0.0', port=80):
    self.addr = addr
    self.port = port
    self.open_socks = []

  async def run(self, loop, sensor):
    self.sensor = sensor
    s_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # server socket
    s_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s_sock.bind((self.addr, self.port))
    s_sock.listen(5)
    self.open_socks.append(s_sock)
    LOG.info('Listening on %s:%d', self.addr, self.port)

    poller = select.poll()
    poller.register(s_sock, select.POLLIN)
    while True:
      if poller.poll(1):  # 1ms block
        client_sock, addr = s_sock.accept()
        LOG.info('Connection from: %s', addr)
        loop.create_task(self.process_connection(client_sock))
        gc.collect()
      await asyncio.sleep_ms(200)

  async def process_connection(self, sock):
    global FAN_FORCE
    global TEMPERATURE_THRESHOLD
    self.open_socks.append(sock)
    sreader = asyncio.StreamReader(sock)
    swriter = asyncio.StreamWriter(sock, '')
    request = None
    try:
      for _ in range(50):     # no reason to have a longer header
        line = await sreader.readline()
        line = line.decode().strip()
        if not line:  # EOF.
          break
        if line.startswith('GET '):
          request = line

      if not request:
        raise OSError('Empty request')

      _, request, _ = request.split()
      LOG.debug('Request: %s', request)
      if request == '/':
        await self.send_page(swriter)
      elif 'force=on' in request:
        FAN_FORCE = True
        FAN.value(ON)
        await self.send_redirect(swriter)
      elif 'force=off' in request:
        FAN_FORCE = False
        FAN.value(OFF)
        await self.send_redirect(swriter)
      elif 'temp=' in request:
        _, val = request.split('=')
        if val.isdigit():
          val = int(val)
          TEMPERATURE_THRESHOLD = val
          await self.send_redirect(swriter)
        else:
          await self.send_error(swriter, request)
      else:
        await self.send_error(swriter, request)
    except OSError:
      pass
    self.open_socks.remove(sock)
    sock.close()

  async def send_redirect(self, wfd):
    LOG.info('Send redirect')
    err_code = (303, 'Response redirect')
    header = ('HTTP/1.1 %d %s' % err_code,
              'Location: /',
              'Content-Type: text/html',
              'Connection: close',
              '\n\n')
    await wfd.awrite('\n'.join(header))
    await wfd.awrite(HTTP_ERR % (err_code * 2))

  async def send_error(self, wfd, request):
    LOG.error('URL Error: "%s"', request)
    err_code = (404, 'Not found')
    header = ('HTTP/1.1 %d %s' % err_code,
              'Content-Type: text/html',
              'Connection: close',
              '\n\n')
    await wfd.awrite('\n'.join(header))
    await wfd.awrite(HTTP_ERR % (err_code * 2))

  async def send_page(self, wfd):
    LOG.debug('Send page')
    fan_states = {0: "Off", 1: "On"}
    force_states = {True: 'Forced', False: "Automatic"}
    header = ('HTTP/1.1 200 OK',
              'Content-Type: text/html',
              'Connection: close',
              '\n\n')
    response = TEMPLATE % (
      self.sensor.temperature, self.sensor.humidity,
      fan_states[FAN.value()], force_states[FAN_FORCE], int(TEMPERATURE_THRESHOLD),
    )
    await wfd.awrite('\n'.join(header))
    await wfd.awrite(response)

  def close(self):
    for sock in self.open_socks:
      sock.close()


async def run_fan(sensor):
  while True:
    LOG.debug('run_fan threshold: %d', TEMPERATURE_THRESHOLD)
    await asyncio.sleep_ms(50)
    if FAN_FORCE:
      if FAN.value() == OFF:
        FAN.value(ON)
      await asyncio.sleep(5)
      continue

    LOG.info("Temp: %0.2f, Humidity: %0.2f, Threshold: %d",
             sensor.temperature, sensor.humidity, TEMPERATURE_THRESHOLD)
    await asyncio.sleep_ms(100)
    if sensor.temperature > TEMPERATURE_THRESHOLD and FAN.value() == OFF:
      FAN.value(ON)
      LOG.debug("Temp ON")
    elif sensor.temperature < TEMPERATURE_THRESHOLD and FAN.value() == ON:
      FAN.value(OFF)
      LOG.debug("Temp OFF")
    await asyncio.sleep(15)


async def heartbeat():
  speed = 125
  led = Pin(2, Pin.OUT, value=1)
  while True:
    led(1)
    await asyncio.sleep_ms(speed)
    led(0)
    await asyncio.sleep_ms(speed*16)


async def timesync():
  while True:
    try:
      ntptime.settime()
    except OSError as err:
      LOG.warning('timesync: %s', err)
      wait_time = 300
    else:
      wait_time = 3600 * 4
    await asyncio.sleep(wait_time)


def ap_connect():
  import wificonfig as wc
  sta_if = network.WLAN(network.STA_IF)
  if sta_if.isconnected():
    return sta_if
  LOG.info('Connecting to network...')
  sta_if.active(True)
  sta_if.connect(wc.SSID, wc.PASSWORD)
  for sleep_time in range(7):
    if sta_if.isconnected():
      LOG.info('Network config: %s', sta_if.ifconfig())
      return sta_if
    time.sleep(1 + sleep_time)
  LOG.debug('Cannot connect, going into deep sleep for a minute')
  machine.deepsleep(1000 * 60)

def main():
  ap_connect()

  sensor = RHSensor(DHT_GPIO)
  server = HTTPServer('0.0.0.0', 80)

  loop = asyncio.get_event_loop()
  loop.create_task(heartbeat())
  loop.create_task(run_fan(sensor))
  loop.create_task(timesync())
  loop.create_task(server.run(loop, sensor))
  try:
    loop.run_forever()
  except KeyboardInterrupt:
    server.close()
    LOG.info('Closing all connections')

if __name__ == "__main__":
    main()
