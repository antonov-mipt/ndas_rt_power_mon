import os, sys, logging, queue, urllib
sys.path.append(os.path.dirname(__file__))
from com_main_module import COMMON_MAIN_MODULE_CLASS
from datetime import datetime
import utils

class MAIN_MODULE_CLASS(COMMON_MAIN_MODULE_CLASS):
    def __init__(self, njsp, trigger_fxn, standalone = False):

        logger_config = {
            'logger_name':'power_monitor',
            'file_name':'power_monitor',
            'files_dir':'/media/sdcard/logs',
            'file_level': logging.INFO,
            'console_level': logging.DEBUG,
            'console_name': None if standalone else 'POWERMON'
        }
        
        config_params = { 
            'config_file_name':'power_monitor.json',
            'default_config': {
                'ip':'localhost',
                'port':'10000',
                'stream':'main',
                'channel':'ch1',
                'threshold':3.5,
                'hysteresis':0.5,
                'phone_number_1':'',
                'phone_number_2':'',
                'phone_number_3':'',
                'low_msg': 'Voltage dropped below threshold',
                'high_msg': 'Voltage raised above threshold'
            }}
        
        self.curr_voltage = 0
        self.curr_state = 'Unknown'
        self.connected = False
        self.reader_id = 'unknown'
        self.njsp_queue = queue.Queue(100)
        self.njsp_params = {
                'reconnect': True,
                'reconnect_period': 60,
                'bson': True,
                'handshake': {
                    'subscriptions':['streams'],
                    'flush_buffer':False,
                    'client_name':'POWERMON'
                }
            }
            
        web_ui_dir = os.path.dirname(__file__)
        super().__init__(standalone, config_params, njsp, logger_config, trigger_fxn = trigger_fxn, web_ui_dir = web_ui_dir)
        self.logger.info('Starting Power monitor module...')
        
    def get_status(self):
        return { 'curr_voltage': self.curr_voltage, 'curr_state': self.curr_state  }
        
    def process_web_ui_cmd(self, cmd):
        if cmd['cmd'] == 'set_config': 
            if cmd['config']['ip'] != self.config.cfg['ip'] or cmd['config']['port'] != self.config.cfg['port']:
                self.njsp.remove(self.reader_id)
                cfg = cmd['config']
                self.reader_id = self.njsp.add_reader(cfg['ip'], cfg['port'], 'PWM', self.njsp_params, self.njsp_queue)
            self.config.set_config(cmd['config'])
            return {'result':'', 'error':False}
        else: return {'result':'Unknown command', 'error':True}     
        
    def get_message(self):
        msg = super().get_message()
        return msg + '%s'%(' (connected)' if self.connected else ' (connecting...)')
        
    def main(self):
        cfg = self.config.cfg
        self.reader_id = self.njsp.add_reader(cfg['ip'], cfg['port'], 'PWM', self.njsp_params, self.njsp_queue)
 
        while not self.shutdown_event.is_set():
            
            try: packet = self.njsp_queue.get(timeout = 1)
            except queue.Empty: continue
        
            try: 
                conn_state = packet[self.reader_id]['connection_state']
                if conn_state == 'connected':
                    self.connected = True
                    utils.remove_error_from_list(self.errors, "No NJSP connection")
                else:
                    self.connected = False
                    utils.add_error_to_list(self.errors, "No NJSP connection")
            except KeyError: pass
            except Exception as e:
                self.logger.warning(repr(e))
                continue
        
            try: vals = packet[self.reader_id]['streams'][cfg['stream']]['samples'][cfg['channel']]
            except KeyError: continue
            except Exception as e:
                self.logger.warning(repr(e))
                continue
            
            try:
                v = int.from_bytes(vals[-4:], byteorder = 'little', signed = True)
                
                self.state_machine(v/1000000)
            except Exception as e:
                self.logger.error(repr(e))
                break
            
            
        self.njsp.remove(self.reader_id)
        self.module_alive = False
        self.logger.debug('Main thread exited')
            
    def state_machine(self, v):
        self.curr_voltage = v;
        th = self.config.cfg['threshold']
        h = self.config.cfg['hysteresis']
        low = th - h
        high = th + h
        
        if self.curr_state == 'Unknown':
            if v < low: self.curr_state = 'Low'
            elif v > high: self.curr_state = 'High'
            
        elif self.curr_state == 'Low?':
            if v > high: self.curr_state = 'High?'
            elif v < low:
                self.curr_state = 'Low'
                number1 = self.config.cfg['phone_number_1']
                number2 = self.config.cfg['phone_number_2']
                number3 = self.config.cfg['phone_number_3']
                msg = self.config.cfg['low_msg']
                now = datetime.now().strftime('%Y/%m/%d %T ')
                self.logger.warning(msg)
                if number1 != '': self.trigger.sms(number1, now + msg)
                if number2 != '': self.trigger.sms(number2, now + msg)
                if number3 != '': self.trigger.sms(number3, now + msg)
                self.trigger.fire()
            
        elif self.curr_state == 'High?':
            if v < low: self.curr_state = 'Low?'
            elif v > high: 
                self.curr_state = 'High'
                number1 = self.config.cfg['phone_number_1']
                number2 = self.config.cfg['phone_number_2']
                number3 = self.config.cfg['phone_number_3']
                msg = self.config.cfg['high_msg']
                now = datetime.now().strftime("%Y/%m/%d %T ")
                self.logger.warning(msg)
                if number1 != '': self.trigger.sms(number1, now + msg)
                if number2 != '': self.trigger.sms(number2, now + msg)
                if number3 != '': self.trigger.sms(number3, now + msg)
                self.trigger.fire()
            
        elif self.curr_state == 'Low':
            if v > high: self.curr_state = 'High?'
            
        elif self.curr_state == 'High':
            if v < low: self.curr_state = 'Low?'
            