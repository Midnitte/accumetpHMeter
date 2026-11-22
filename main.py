import threading
import time
from datetime import datetime
import re
import pyvisa
import pyvisa_py
import serial
from serial.tools import list_ports
import logging
import csv
import io
from nicegui import ui

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ar50")

latest_reading = {
    'pH': "--",
    'mV': "--",
    'Temperature': "--",
    'Slope': "--",
    'Status': "Connecting...",
}
current_readings = []



def to_float(text):
    match = re.search(r'-?\d+(\.\d+)?', text or '')
    return float(match.group()) if match else None

def parse_ar50_block(lines):
    data = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue

        if "/channel " in line:
            parts = line.split("/")
            if len(parts) == 3:
                data["timestamp"] = f"{parts[0].strip()} {parts[1].strip()}"
                data["channel"] = parts[2].strip()

        elif ":" in line:
            key, val = line.split(":", 1)
            data[key.strip()] = val.strip()
    return data

def serial_reader_thread():
    rm = pyvisa.ResourceManager("@py")
    while True:
        inst = None
        try:
            ftdi_ports = [
                p.device for p in list_ports.comports()
                if "FTDI" in (p.manufacturer or "") or "ttyUSB" in p.device
            ]

            if not ftdi_ports:
                latest_reading['Status'] = "No instrument found! Retrying..."
                time.sleep(3)
                continue

            visa_address = f"ASRL{ftdi_ports[0]}::INSTR"
            inst = rm.open_resource(visa_address)
            inst.baud_rate = 9600
            inst.read_termination = "\n"
            inst.write_termination = "\n"
            inst.timeout = 2000
            latest_reading['Status'] = "Connected"
            logger.info("Connected to %s. Waiting for Accumet AR50 readings...", visa_address)
            buffer = []

            while True:
                try:
                    raw = inst.read()
                except pyvisa.errors.VisaIOError as e:
                    if e.error_code == pyvisa.constants.StatusCode.error_timeout:
                        # Waiting on readings from instrument, loop until reading received or error occurs
                        continue
                    raise

                for line in raw.splitlines():
                    clean = line.strip()
                    if not clean:
                        continue
                    buffer.append(clean)

                    if clean.startswith("Operator:"):
                        reading = parse_ar50_block(buffer)
                        if reading['Sample ID'] == '':
                            reading['Sample ID'] = datetime.now().isoformat()
                        logging.info("Parsed Reading: %s", reading)
                        latest_reading.update({
                            'pH': to_float(reading.get('pH measurement')),
                            'mV': to_float(reading.get('mV measurement')),
                            'Temperature': to_float(reading.get('Temperature')),
                            'Slope': to_float(reading.get('Slope')),
                            'Status': "Connected",
                        })
                        current_readings.append((reading.get('pH measurement', "--"), reading.get('Sample ID', datetime.now().isoformat()), datetime.now().isoformat()))
                        buffer.clear()

        except (serial.SerialException, pyvisa.errors.VisaIOError, OSError) as e:
            logging.error("Error occurred while reading from instrument: %s", e)
            latest_reading['Status'] = "Disconnected. Retrying..."
        except KeyboardInterrupt:
            logging.info("Closing connection.")
        finally:
            if inst:
                try:
                    inst.close()
                except Exception:
                    pass

threading.Thread(target=serial_reader_thread, daemon=True).start()


@ui.page('/')
def page_layout():
    with ui.row().classes('no-wrap !items-stretch'):
        with ui.card().classes('w-96'):
            ui.label('Latest reading').classes('text-lg font-bold')
            ph_label = ui.label()
            mv_label = ui.label()
            temp_label = ui.label()
            slope_label = ui.label()
        with ui.card().classes('w-96'):
            chart = ui.echart({
                'xAxis': {'type': 'time', 'axisLabel': {'formatter': '{HH}:{mm}:{ss}', 'rotate': 30}},
                'yAxis': {'type': 'value', 'name': 'pH', 'min': 0},
                'tooltip': {'trigger': 'axis'},
                'series': [{'type': 'line', 'name': 'pH', 'data': []}],
            }).classes('w-full h-64')
    def refresh():
        ph_label.text = f"pH: {latest_reading['pH']}"
        mv_label.text = f"mV: {latest_reading['mV']}"
        temp_label.text = f"Temperature: {latest_reading['Temperature']}℃"
        slope_label.text = f"Slope: {latest_reading['Slope']}%"
        status_label.text = f"Status: {latest_reading['Status']}"
        points = []
        for ph, sample_id, timestamp in current_readings:
            try:
                points.append([timestamp, float(ph)])
            except (TypeError, ValueError):
                continue
        chart.options['series'][0]['data'] = points
        chart.update()
        table.rows = [
            {'sample_id': sample_id, 'pH': pH}
            for pH, sample_id, timestamp in current_readings
        ]
        table.update()
    with ui.card().classes('w-96'):
        ui.label('Data Table').classes('text-lg font-bold')
        columns = [
            {'name': 'sample_id', 'label': 'Sample ID', 'field': 'sample_id', 'align': 'left'},
            {'name': 'pH', 'label': 'pH', 'field': 'pH', 'align': 'left'},
        ]
        table = ui.table(columns=columns, rows=[], row_key='sample_id').classes('w-full')
    def clear_data():
        current_readings.clear()
        table.rows = []
        table.update()
        latest_reading.update({
            'pH': "--",
            'mV': "--",
            'Temperature': "--",
            'Slope': "--",
            'Status': "Connecting...",
        })
        refresh()
    with ui.header(elevated=True).style('background-color: #3874c8').classes('items-center justify-between'):
        ui.label('AR50 pH Meter Readings')
        ui.button(on_click=lambda: right_drawer.toggle(), icon='menu').props('flat color=white')
    def download_csv():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['pH', 'Sample ID', 'Timestamp'])
        writer.writerows(current_readings)
        # TODO: Add Operator? Maybe as input in right drawer
        
        ui.download.content(output.getvalue(), filename=f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv", media_type='text/csv')
    with ui.right_drawer(fixed=False).classes('!bg-blue-900 !text-white') as right_drawer:
        status_label = ui.label()
        with ui.row():
            ui.button('Reset', on_click=clear_data, icon='history')
            ui.button('Export', on_click=download_csv, icon='save')
        # TODO: Add scaling options for chart
        # TODO: Export chart?

    with ui.footer().style('background-color: #3874c8'):
        ui.label('Chris Norulak - 2026')

    ui.timer(1, refresh)


ui.run(reload=False, title="AR50 pH Meter Readings", dark=True)
