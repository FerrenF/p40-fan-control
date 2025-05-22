import subprocess
import logging

import requests
import time
import os
import signal


# Fan control script for a PI controlled server fan running over a P40 within a server case.

pi_address = '10.50.55.24'

# Set up basic logging. Absolute path if this is going to be a service.
logging.basicConfig(filename='/home/hwilliams/gpu_fan_control.log', level=logging.INFO, format='%(asctime)s - %(message)s')

def get_gpu_processes():

    # Query running compute apps on the GPU using the nvidia-smi app
    result = subprocess.run(
        ['nvidia-smi', '--query-compute-apps=pid,process_name,used_memory', '--format=csv,noheader'],
        stdout=subprocess.PIPE,
        text=True
    )

    processes = result.stdout.strip().splitlines()
    gpu_processes = []

    for process in processes:
        pid, process_name, used_memory = process.split(',')
        gpu_processes.append({
            'pid': pid.strip(),
            'process_name': process_name.strip(),
            'used_memory': used_memory.strip()
        })
    
    return gpu_processes


# Try to kill a process for whatever reason.
def kill_gpu_process(pid):
    try:
        os.kill(int(pid), signal.SIGTERM)  # Graceful kill
        logging.info(f"Successfully killed process with PID {pid}")
        return True
    except OSError as e:
        logging.error(f"Error killing process {pid}: {e}\n attempting force kill...")
        try:
            os.kill(int(pid), 9)  # Not-so-graceful kill
            logging.warning(f"Successfully killed process with PID {pid}")
            return True
        except OSError as e:
            logging.error(f"Could not force kill process {pid}: {e}")
        return False
    

# Try to sigterm (Signal termination) for all processes on the GPU.
def sigterm_all_gpu_processes():
    processes = get_gpu_processes()
    status = True
    for process in processes:
        logging.warning(f"Killing PID {process['pid']}:{process['process_name']}")
        if not kill_gpu_process(process['pid']):
            status = False
    return status



def get_gpu_utilization():
    # Call nvidia-smi and parse utilization
    result = subprocess.run(['nvidia-smi', '--query-gpu=utilization.gpu', '--format=csv,noheader,nounits'],
                            stdout=subprocess.PIPE, text=True)
    utilization = int(result.stdout.strip())  # Utilization is returned as an integer (0-100)
    return utilization



def is_gpu_in_use(threshold=10):
    # Consider GPU to be in use if utilization is above the threshold
    utilization = get_gpu_utilization()
    return utilization > threshold



def get_gpu_temperature():
    result = subprocess.run(['nvidia-smi', '--query-gpu=temperature.gpu', '--format=csv,noheader,nounits'],
                            stdout=subprocess.PIPE, text=True)
    temperature = int(result.stdout.strip())
    return temperature

define_hot = 65 # Celsius, of course    
fan_timeout_count = 0
fan_timeout_kill_threshold = 3
process_kill_sent = False
def timeout_count_check(timeout=False):
    global fan_timeout_count, fan_timeout_kill_threshold, process_kill_sent
    if timeout:
        fan_timeout_count+=1
    else:
        if process_kill_sent:
            process_kill_sent = False
        if fan_timeout_count > 0:
            fan_timeout_count = 0
    if fan_timeout_count > fan_timeout_kill_threshold:
        if not process_kill_sent:
            logging.warning(f"Fan has not responded for {fan_timeout_kill_threshold} iterations. Killing all GPU processes.")
            process_kill_sent=True
            while not sigterm_all_gpu_processes():
                time.sleep(5)
        
def get_fan_status():
    try:
        url = f'http://{pi_address}:8080/status'
        response = requests.get(url)
        if response.status_code == 200:
            timeout_count_check()
            if 'OFF' in str(response.content):
                return 0
            if 'ON' in str(response.content):
                return 1
    except requests.Timeout as ex:
        timeout_count_check(True)
        logging.exception("Connection to fan controller timed out.", ex)
        
    return -1
    
def control_blower_fan(action):
    if str(action) not in ['on','off']:
        print('Invalid command')
        return -1
    
    try:
        url = f'http://{pi_address}:8080/control' 
        data = {'status': action} 
        response = requests.get(url, params=data)
        timeout_count_check()
        return response.status_code == 200
    except requests.Timeout as ex:
        timeout_count_check(True)
        logging.exception("Connection to fan controller timed out.", ex)
    return -1
    
    
    
def monitor_gpu_and_control_fan(use_threshold=15, check_interval=20):
  
    while True:
        utilization = get_gpu_utilization()
        temperature = get_gpu_temperature()
        fan_status = get_fan_status()
        
        def is_gpu_getting_hot():
            global define_hot
            return temperature >= define_hot

        if is_gpu_in_use(threshold=use_threshold) or is_gpu_getting_hot():
            if not fan_status == 1:
                logging.info(f'GPU in use - Utilization: {utilization}%, Temperature: {temperature}°C. Turning on blower fan.')
                if control_blower_fan('on'):
                    fan_on = True
        else:
            if fan_status == 1:
                logging.info(f'GPU idle - Utilization: {utilization}%, Temperature: {temperature}°C. Turning off blower fan.')
                if control_blower_fan('off'):
                    fan_on = False

        time.sleep(check_interval)
        
if __name__ == "__main__":
    monitor_gpu_and_control_fan()

