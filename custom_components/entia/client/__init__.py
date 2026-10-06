"""Entia Client"""
from __future__ import annotations

import time
import re
from collections import deque
import logging
import json
import asyncio
import httpx

_LOGGER = logging.getLogger(__name__)

# Number of recent request/response exchanges kept for error reports
HISTORY_SIZE = 10
# Maximum characters of a single request/response printed in an error report
HISTORY_MAX_CHARS = 2000


class EntiaClient:
    """Entia Client"""

    def __init__(self, username: str, password: str):
        self._username = username
        self._password = password
        self._history: deque[dict] = deque(maxlen=HISTORY_SIZE)

    async def start(self, create_task=asyncio.create_task, task=None):
        """Main loop"""
        if self._task:
            raise AlreadyRunning("Client already running")
        if task:
            self._task = task
        else:
            self._task = create_task(self.loop())

    async def stop(self):
        """Stop the client loop"""

        _LOGGER.info(f"Stopping {self._flat_id}")

        self._running = False
        self._ready = False

        self._cleanup()

        if not self._task or self._task.done():
            return

        await self._task

    def _cleanup(self):
        # cancel all awaited signals
        for signal in self._signals:
            if signal != 'stop':
                self._signals[signal].set()

        # remove all callbacks
        self._callbacks = {}

    _last_error = None
    _running = False
    _connected = False
    _ready = False
    _task = None
    _last_refresh = None

    _pipe_id = None
    _session_id = None
    _counter = 0

    async def loop(self):

        if self._running:
            raise AlreadyRunning("Client already running")

        self._running = True
        self._connected = False
        received_data = []

        try:
            while self._running:
                if not self._connected:
                    await self._connect()
                    self._connected = True

                    self._queue = [{
                        'cmd': 'FLAT_GET_TEMPLATE',
                    }]  # clear queue, there could be dragons
                else:
                    for data in received_data:
                        if data['raw'] in ['ERR', 'FLAT_ERROR', 'FLAT_DISCONNECT', 'QUIT', 'DEVICE_SET_EXT']:
                            _LOGGER.error("Server Error: %s\n%s", received_data, self.format_history())
                            self._ready = False
                            self._connected = False

                            # logical error, wait a bit before reconnecting
                            await asyncio.sleep(10)

                            break  # for

                        elif data['raw'] == 'FLAT_TEMPLATE':
                            try:
                                self._apply_template(data['data']['msg'])

                                # get latest data
                                self._last_refresh = time.time()
                                await self._enqueue(
                                    {'cmd': 'FLAT_GET', 'dedup': 'refresh', 'extra': ['"list":"all"', '"extended":1']})

                            except Exception as e:
                                _LOGGER.warning("Malformed template: %s\n%s", received_data, self.format_history(),
                                                exc_info=True)
                                pass

                        elif data['raw'] == 'FLAT_NOTIFY' or data['raw'] == 'FLAT_EVENT':
                            try:
                                await self._apply_states(data['data']['msg'])

                            except Exception as e:
                                _LOGGER.warning("Malformed states: %s\n%s", received_data, self.format_history(),
                                                exc_info=True)
                                pass

                            if data['raw'] == 'FLAT_NOTIFY':
                                if not self._ready:
                                    # got the first batch of data
                                    self._ready = True
                                    await self._trigger('ready')
                                    _LOGGER.info(f"Resolved flat {self._flat_id}")

                        elif data['raw'] in ['FLAT_WEATHER', 'JOIN', 'LEFT', 'FLAT_HEARTBEAT', 'CHANNEL', 'DEVICE_SET',
                                             'FLAT_SETTING_GET', 'FLAT_SET', 'CLOSE']:
                            # not interesting
                            pass

                        else:
                            # something new!
                            _LOGGER.warning(f"Unexpected data: %s", data)

                if not self._connected:
                    continue  # while

                if not self._running:
                    break  # while

                # bookkeeping
                refresh = False
                refresh_age = time.time() - self._last_refresh if self._last_refresh else None
                if refresh_age and refresh_age > 3600:
                    refresh = True  # full refresh every hour
                elif refresh_age and refresh_age > 120:
                    for cover in [x for x in self.data.values() if x['type'] == "cover" and x['state'] != 'stopped']:
                        if cover['last_update'] < time.time() - 120:
                            refresh = True  # cover has been moving for more than 2 minutes, probably a bug
                            break
                if refresh:
                    self._last_refresh = time.time()
                    await self._enqueue(
                        {'cmd': 'FLAT_GET', 'dedup': 'refresh', 'extra': ['"list":"all"', '"extended":1']})

                command = 'CHECK'
                extra = None
                cancelable = True
                if len(self._queue) > 0:
                    next_command = self._queue.pop(0)
                    command = next_command['cmd']
                    extra = next_command['extra'] if 'extra' in next_command else None
                    cancelable = True if 'cancelable' in next_command else False
                try:
                    received_data = await self._send(command=command, extra=extra, cancelable=cancelable)
                except Exception as e:
                    # network error
                    _LOGGER.exception("Network Error\n%s", self.format_history())
                    self._ready = False
                    self._connected = False
                    continue  # while

            # end while

        except Exception as e:
            e.add_note(self.format_history())
            _LOGGER.error("Client stopped on error", exc_info=e)
            self._last_error = e

        finally:
            self._running = False
            self._ready = False

            self._cleanup()

            self._task = None

            _LOGGER.info(f"Stopped {self._flat_id}")

    async def _connect(self):
        """Connect to the server"""

        wait_for = 1
        while self._running:

            self._counter = 1
            self._pipe_id = None
            self._session_id = None
            try:
                received_data = await self._send('CONNECT', ['"name":null'])
            except Exception as e:
                # this is probably a network error
                _LOGGER.debug(f"Error connecting", exc_info=True)
                wait_for *= 2
                await asyncio.sleep(wait_for)
                continue
            self._pipe_id = None
            self._session_id = None

            for received_data_item in received_data:
                if received_data_item['raw'] == 'LOGIN':
                    # STEP 1 RESPONSE PART 1
                    self._session_id = received_data_item['data']['sessid']

                elif received_data_item['raw'] == 'IDENT':
                    # STEP 1 RESPONSE PART 2
                    if self._pipe_id:
                        # second CHANNEL message (PART 3), ignore
                        # todo, research what this is
                        break
                    self._pipe_id = received_data_item['data']['user']['pubid']
                else:
                    raise RuntimeError("Unexpected response", received_data)

            if not self._pipe_id or not self._session_id:
                raise RuntimeError("No pipe or session ID received")

            # STEP 2
            try:
                received_data = await self._send('FLAT_CONNECT', [
                    f'"username":"{self._username}"',
                    f'"password":"{self._password}"'
                ])
            except Exception as e:
                # this is probably a network error
                _LOGGER.exception("Error connecting\n%s", self.format_history())
                wait_for *= 2
                await asyncio.sleep(wait_for)
                continue

            for received_data_item in received_data:

                if received_data_item['raw'] == 'FLAT_LOGIN':
                    # connected and logged in
                    self._flat_id = received_data_item['data']['flatid']
                    # self._session_key = received_data_item['data']['sessionkey']
                    # self._login_hash = received_data_item['data']['login_hash']
                    await self._trigger('connected')
                    _LOGGER.info(f"Connected to flat {self._flat_id}")
                    return

                elif received_data_item['raw'] == 'FLAT_ERROR' and received_data_item['data']['reason'] == 'ERROR 401':
                    # bad credentials
                    raise BadCredentials("Bad Credentials")
                else:
                    raise RuntimeError("Unexpected response", received_data)

            raise RuntimeError("Unexpected response", received_data)

    async def _send(self, command: str, extra: list[str] | None, cancelable: bool = False):
        """Send a formatted message"""

        if not self._running:
            raise ClientStopped("Client stopped")

        message_parts = [
            f'"cmd":"{command}"',
            f'"chl":{self._counter}'
        ]
        self._counter += 1
        if self._session_id:
            message_parts.append(f'"sessid":"{self._session_id}"')
        param_parts = [
            f'"time":"{str(int(time.time()))}"'
        ]
        if self._pipe_id:
            param_parts.append(f'"pipe":"{self._pipe_id}"')
        if extra:
            param_parts.extend(extra)
        message_parts.append(f'"params":{{{",".join(param_parts)}}}')

        return await self._send_raw(message='[{' + ','.join(message_parts) + '}]', cancelable=cancelable)

    _send_raw_window = time.time()  # timestamp of last window
    _send_raw_messages = 0  # messages in window
    _send_raw_cancelable = None

    async def _send_raw(self, message: str, cancelable: bool = False):
        """"Send a raw message"""

        # rate limit
        self._send_raw_messages += 1
        now = time.time()
        if now - self._send_raw_window > 20:
            self._send_raw_window = now
            self._send_raw_messages = 1
        elif self._send_raw_messages > 20:
            _LOGGER.debug("Rate limiting")
            await asyncio.sleep(20 - (now - self._send_raw_window))
            self._send_raw_window = time.time()
            self._send_raw_messages = 1

        if not self._running:
            raise ClientStopped("Client stopped")

        entry = {
            'time': time.time(),
            'request': self._redact(message),
            'status': None,
            'response': None,
            'error': None,
            'duration': None,
        }
        self._history.append(entry)

        async with httpx.AsyncClient() as client:
            if cancelable:
                self._send_raw_cancelable = client

            try:
                res = await client.get(
                    'https://ape.entia.si/2/?' + message,
                    headers={
                        'Accept': '*/*',
                        'Accept-Encoding': 'gzip, deflate',
                        'Cache-Control': 'no-cache',
                        'Connection': 'keep-alive',
                        'Host': 'ape.entia.si',
                        'Pragma': 'no-cache',
                        'Referer': 'https://dostop.entia.si/',
                        'sec-ch-ua': '"Google Chrome";v="89", "Chromium";v="89", ";Not A Brand";v="99"',
                        'sec-ch-ua-mobile': '?0',
                        'Sec-Fetch-Dest': 'script',
                        'Sec-Fetch-Mode': 'no-cors',
                        'Sec-Fetch-Site': 'same-site',
                        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.82 Safari/537.36'
                    },
                    timeout=120  # long polling
                )
            except Exception as e:
                entry['duration'] = time.time() - entry['time']
                entry['error'] = repr(e)
                self._log_exchange(entry)
                if cancelable:
                    self._send_raw_cancelable = None
                    return []
                raise RuntimeError("Error sending message", e)

            self._send_raw_cancelable = None
            res_text = res.text

        entry['duration'] = time.time() - entry['time']
        entry['status'] = res.status_code
        entry['response'] = self._redact(res_text)
        self._log_exchange(entry)

        res_json = self._decode(res_text)
        if not res_json or not isinstance(res_json, list):
            raise RuntimeError("Response malformed", {res_text: res_text})

        return res_json

    @staticmethod
    def _decode(res_text: str):
        """Decode an APE response body, None if malformed"""
        match = re.search(r'^Ape\.transport\.read\(\'(.+)\'\)', res_text)
        if not match:
            return None
        try:
            return json.loads(match.group(1))
        except ValueError:
            return None

    # Diagnostics

    @staticmethod
    def _redact(text: str) -> str:
        """Remove credentials and session identifiers from a logged message"""
        # login_hash is the Entia ID (the username); sessid and pipe identify the live session
        return re.sub(r'"(username|password|login_hash|sessionkey|sessid|pubid|pipe)":\s*"[^"]*"',
                      r'"\1":"**REDACTED**"', text)

    def _log_exchange(self, entry: dict):
        """Log an exchange as one JSON line, the format tests replay from debug logs"""
        if not _LOGGER.isEnabledFor(logging.DEBUG):
            return

        try:
            request = json.loads(entry['request'])[0]
        except (ValueError, IndexError, KeyError):
            request = entry['request']
        response = None
        if entry['response'] is not None:
            response = self._decode(entry['response'])
            if response is None:
                response = entry['response']  # malformed, keep raw text

        _LOGGER.debug("Exchange %s", json.dumps({
            'request': request,
            'response': response,
            'status': entry['status'],
            'duration': round(entry['duration'], 3) if entry['duration'] is not None else None,
            'error': entry['error'],
        }, separators=(',', ':'), ensure_ascii=False))

    @property
    def history(self) -> list[dict]:
        """Most recent request/response exchanges, oldest first, with credentials redacted"""
        return [dict(x) for x in self._history]

    def format_history(self) -> str:
        """Format the most recent exchanges for an error report"""

        def clip(text: str) -> str:
            if len(text) <= HISTORY_MAX_CHARS:
                return text
            return text[:HISTORY_MAX_CHARS] + f'... ({len(text) - HISTORY_MAX_CHARS} more chars)'

        if not self._history:
            return "No Entia exchanges recorded"

        lines = [f"Last {len(self._history)} Entia exchanges (oldest first):"]
        for i, entry in enumerate(self._history, 1):
            cmd = re.search(r'"cmd":"(\w+)"', entry['request'])
            when = time.strftime('%H:%M:%S', time.localtime(entry['time']))
            duration = f"{entry['duration']:.2f}s" if entry['duration'] is not None else 'pending'
            status = f" HTTP {entry['status']}" if entry['status'] is not None else ''
            lines.append(f"#{i} {when} {cmd.group(1) if cmd else '?'} ({duration}){status}")
            lines.append(f"  > {clip(entry['request'])}")
            if entry['response'] is not None:
                lines.append(f"  < {clip(entry['response'])}")
            if entry['error'] is not None:
                lines.append(f"  ! {entry['error']}")
        return '\n'.join(lines)

    # Device Data

    async def _update_data(self, unique_id: str, data: dict, trigger=True):
        """Set data"""
        if unique_id not in self.data:
            self.data[unique_id] = data
        else:
            self.data[unique_id].update(data)

        # last update
        self.data[unique_id]['last_update'] = time.time()

        if trigger:
            await self._trigger(unique_id)

    data = {}

    def _apply_template(self, template: dict):
        """Parse and apply the template"""
        rooms = template['hause']['floors']['floor']['rooms']['room']
        for room in rooms:
            for element in room['elements']['element']:

                if 'id' not in element or 'type' not in element or 'label' not in element:
                    continue

                unique_id = f'{self._flat_id}-{element["id"]}'

                label = element['label']
                element_type = element['type']
                element_subtype = element['subtype'] if 'subtype' in element else None
                device_type = None

                if element_type == 4 and element_subtype == 1:
                    device_type = 'light'
                elif element_type == 4 and element_subtype == 4:
                    device_type = 'fan'
                elif element_type == 6:
                    device_type = 'cover'
                # elif type == 5:
                #    type = 'dimmer'
                # elif type == 11:
                #    type = 'hmi'
                # elif type == 18:
                #    type = 'urnik'

                if device_type:
                    data = {
                        'unique_id': unique_id,
                        'device_id': self._flat_id,
                        'id': element['id'],
                        'name': label,
                        'type': device_type,
                    }
                    if unique_id not in self.data:
                        self.data[unique_id] = data
                    else:
                        self.data[unique_id].update(data)

    async def _apply_states(self, state: dict):
        """Apply states"""

        try:
            devices = state['response']['device']
        except KeyError:
            # empty response
            return

        if not isinstance(devices, list):
            # api bug
            return

        for element in devices:
            if 'id' not in element:
                continue
            unique_id = f'{self._flat_id}-{element["id"]}'
            if unique_id not in self.data:
                # not interesting
                continue

            element_type = element['type']
            device_type = self.data[unique_id]['type']

            if device_type == 'cover':
                if element_type != 6:
                    raise RuntimeError(f"Unexpected element type for {unique_id}: {element_type}")
                element_direction = next((x for x in element['attribute'] if x['id'] == 3))['val']
                if element_direction == 1:
                    element_state = 'opening'
                elif element_direction == 2:
                    element_state = 'closing'
                else:
                    element_state = 'stopped'
                # position = next((x for x in element['attribute'] if x['id'] == 2))['val']
                element_position = 100 - next((x for x in element['attribute'] if x['id'] == 1))['val']
                if element_position < 0:
                    element_position = 0
                elif element_position > 100:
                    element_position = 100

                await self._update_data(unique_id, {'position': element_position, 'state': element_state}, True)

            elif device_type == 'light' or device_type == 'fan':
                if element_type != 4:
                    raise RuntimeError(f"Unexpected element type for {unique_id}: {element_type}")
                element_state = 'on' if next((x for x in element['attribute'] if x['id'] == 1))['val'] == 1 else 'off'
                await self._update_data(unique_id, {'state': element_state}, True)

            # elif device_type == 'dimmer':
            #    raise RuntimeError("Not implemented")

    # Device Control

    _queue = []

    async def _enqueue(self, data):
        """Enqueue a command"""

        # use last request
        if 'dedup' in data:
            self._queue = [x for x in self._queue if 'dedup' not in x or x['dedup'] != data['dedup']]

        self._queue.append(data)

        if 'cancelable' not in data and self._send_raw_cancelable:
            # close health check long polling
            await self._send_raw_cancelable.aclose()

    async def request(self, unique_id: str, state):
        """Request to change the state of the device"""

        if not self._ready or not self._running:
            _LOGGER.debug(f"Not ready")
            return

        if unique_id not in self.data:
            _LOGGER.warning(f"Device not found: {unique_id}")
            return

        device = self.data[unique_id]

        _LOGGER.info(f"Set {device['type']}.{device['name']} ({device['unique_id']}) to {state}")

        if device['type'] == 'cover':
            if 'position' in state:
                position = 100 - state['position']
                if position < 0:
                    position = 0
                elif position > 100:
                    position = 100
                await self._enqueue({
                    'cmd': 'DEVICE_SET',
                    'dedup': device['unique_id'],
                    'extra': [
                        f'"deviceid": "{device["id"]}"',
                        '"data":{"attribute":[{"id": 1,"val":' + str(position) + '}]}'
                    ]
                })
            elif 'state' in state:
                if state['state'] == 'stopped':
                    await self._enqueue({
                        'cmd': 'DEVICE_SET',
                        'dedup': device['unique_id'],
                        'extra': [
                            f'"deviceid": "{device["id"]}"',
                            '"data":{"attribute":[{"id": 3,"val": 0}]}'
                        ]
                    })
                else:
                    raise RuntimeError(f"Unsupported state: {state}")
        elif device['type'] == 'light' or device['type'] == 'fan':
            value = '1' if state['state'] == 'on' else '0'
            await self._enqueue({
                'cmd': 'DEVICE_SET',
                'dedup': device['unique_id'],
                'extra': [
                    f'"deviceid": "{device["id"]}"',
                    '"data":{"attribute":[{"id": 1,"val":' + value + '}]}'
                ]
            })

    # Device Information

    _flat_id = None

    @property
    def serial(self):
        if not self._flat_id:
            raise RuntimeError("Client not ready")
        return self._flat_id

    @property
    def name(self):
        return "Entia"

    @property
    def model(self):
        return "Entia"

    @property
    def manufacturer(self):
        return "Entia Living"

    @property
    def online(self):
        return self._ready and self._running

    # Signals and Events

    _signals = {}
    _callbacks = {}

    async def _trigger(self, event: str):
        """Trigger a signal"""

        # _LOGGER.debug(f"Triggering {event}")

        if event in self._callbacks:
            try:
                self._callbacks[event]()
            except Exception as e:
                _LOGGER.exception(f"Error in callback {event}")

        if event in self._signals:
            self._signals[event].set()
            del self._signals[event]

    def on(self, event: str, callback):
        """Register a callback"""
        if event in self._callbacks:
            raise RuntimeError(f"Callback {event} already registered")
        self._callbacks[event] = callback

    def off(self, event: str):
        """Deregister a callback"""
        del self._callbacks[event]

    async def wait(self, event: str):
        """Wait for a signal"""

        if event not in self._signals:
            self._signals[event] = asyncio.Event()
        await self._signals[event].wait()

        if event == 'stop':
            return

        if not self._running:
            if self._last_error:
                raise self._last_error
            raise ClientStopped("Client stopped")


class BadCredentials(Exception):
    pass


class ClientStopped(Exception):
    pass


class AlreadyRunning(Exception):
    pass
