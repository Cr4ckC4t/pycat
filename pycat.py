#!/usr/bin/env python3

import sys
import socket
import subprocess
import argparse
import os
import signal
import threading
import platform

# control verbosity with the -v flag
verbosity = False

# print a message only in verbose mode
def vprint(*args, **kwargs):
	if verbosity:
		print(*args, **kwargs)

# set up a shell process and connect it to a socket
class LocalShell:

	# @param sock: the socket to connect the shell to
	def __init__(self, sock):
		self.sock = sock
		self.os = platform.system()
		# choose a shell depending on the OS
		if self.os == 'Windows':
			self.shell_exe = 'cmd.exe'
		elif self.os == 'Linux':
			self.shell_exe = '/bin/bash'
		else:
			self.shell_exe = ''
			print(f'Warning, no shell configured for this OS: {self.os}', file=sys.stderr)
			sys.exit(1)

		# set up trap for keyboard interrupts
		signal.signal(signal.SIGINT, self.exit)
		signal.signal(signal.SIGTERM, self.exit)

		self.p_shell = None

	# executed in a seperate thread, this function forwards
	# the output of the local shell to the connected socket
	# @param proc: the shell process to watch
	def get_results(self, proc):
		try:
			while True:
				output = proc.stdout.read(1)
				if not output:
					break
				self.sock.send(output)
		except:
			# any error should just end this thread
			pass


	def run(self):

		# create shell process
		env = os.environ.copy()
		self.p_shell = subprocess.Popen(self.shell_exe, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True, env=env)

		# start thread for reading output of shell
		responder = threading.Thread(target=self.get_results, args=(self.p_shell,), daemon=True)
		responder.start()

		# send input to shell
		while True:
			# retrieve cmd from client
			try:
				cmd = self.sock.recv(4096)
			except:
				break

			# terminate shell
			if not cmd:
				break

			# replace windows carriage return when target is a linux
			if self.os == 'Linux':
				cmd = cmd.replace(b'\r', b'\n')

			# send cmd to shell process to be executed
			self.p_shell.stdin.write(cmd)
			self.p_shell.stdin.flush()

		# close program
		self.exit()


	# handle exit of bind shell gracefully
	def exit(self, *args):
		# terminate thread
		self.p_shell.terminate()
		try:
			# close connection
			self.sock.shutdown(socket.SHUT_RDWR)
			self.sock.close()
		except:
			pass
		sys.exit(0)

class PyCat:
	def __init__(self, args):
		global verbosity
		verbosity = args.verbose

		self.shell = args.shell
		self.listen = args.listen
		self.target = args.ip
		self.port = args.port

		# create communication socket
		self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

		# socket for peer
		self.con = None

		if self.listen or self.shell:
			# bind to ip:port
			# communication via self.con
			self.set_up_server(self.target, self.port)
		else:
			# connect to ip:port
			# communication via self.sock
			self.connect_to_server(self.target, self.port)

	# create a server and wait for a connection
	def set_up_server(self, target, port):
		try:
			self.sock.bind((target, port))
			self.sock.listen(1)
			vprint(f'Listening on [{self.target}:{self.port}]', file=sys.stderr)
			self.con, remote = self.sock.accept()
			vprint(f'Received connection from [{remote[0]}:{remote[1]}]', file=sys.stderr)
			self.sock.close()
		except Exception as e:
			print(f'(Error) Could not setup server: {e}', file=sys.stderr)
			self.close_socks(1)
		except KeyboardInterrupt:
			self.close_socks()

	# attempt to establish a connection to target:port
	def connect_to_server(self, target, port):
		try:
			self.sock.connect((target, port))
		except Exception as e:
			print(f'(Error) Failed to connect: {e}', file=sys.stderr)
			sys.exit(1)
		except KeyboardInterrupt:
			print()
			sys.exit(0)

	# attempt to handle an arbitrary exit gracefully
	def close_socks(self, ret=0):
		# close con if it exists
		try:
			self.con.shutdown(socket.RDWR)
			self.con.close()
		except:
			pass
		# close sock if it exists
		try:
			self.sock.shutdown(socket.RDWR)
			self.sock.close()
		except:
			pass
		# use os._exit instead of sys.exit to close all threads as well
		os._exit(ret)

	# initiate the server/client
	def run(self):
		try:
			# if both -l and -s are specified, -s will dominate
			if self.shell:
				sh = LocalShell(self.con)
				sh.run()
			elif self.listen:
				self.listener()
			else:
				self.sender()
		except Exception as e:
			print(f'Closing unexpectedly: {e}', file=sys.stderr)
			self.close_socks(1)

	# plain listener that redirects everything it receives to stdout
	def listener(self):
		try:
			while True:
				rec = self.con.recv(4096)
				if not rec:
					break
				sys.stdout.buffer.write(rec)
				sys.stdout.buffer.flush()
		except KeyboardInterrupt:
			self.close_socks()

	# executed in a seperate thread, this function forwards every
	# response from the target directly to stdout
	def _recv(self):
		while True:
			try:
				resp = self.sock.recv(4096)
				if not resp:
					break
				sys.stdout.buffer.write(resp)
				sys.stdout.buffer.flush()
			except:
				break
		self.close_socks()

	# client for the listener/bind shell
	def sender(self):
		# start a thread to process responses
		reader = threading.Thread(target=self._recv, daemon=True)
		reader.start()

		vprint(f'Connection established [{self.target}:{self.port}]', file=sys.stderr)

		# read from stdin and send it to the target
		while True:
			try:
				d = sys.stdin.buffer.read(1)
				if not d:
					# CTRL + D
					break
				self.sock.send(d)
			# CTRL + C (CTRL + break in windows)
			except KeyboardInterrupt:
				break
		self.close_socks()

if __name__ == '__main__':
	name = sys.argv[0]
	parser = argparse.ArgumentParser(
		description = 'PyCat (Network Tool)',
		formatter_class=argparse.RawDescriptionHelpFormatter,
		epilog = f'''Examples:
	(Binary) File Transfer
		{name} -l > out.dat          # Set up a listener
		{name} -t [target] < in.dat  # Send the file

	Bind Shell
		{name} -s           # Set up a bind shell
		{name} -t [target]  # Connect to the target 

	Options
		Use -p to specify the target/bind port.
		Use -t to specify the target/bind address.
		Use -v to add some verbosity.

		''')
	parser.add_argument('-t', '--target', default='0.0.0.0', help='target/bind address (default: 0.0.0.0)', dest='ip')
	parser.add_argument('-p', '--port', type=int, default=4433, help='port to use (default:4433)')
	parser.add_argument('-l', '--listen', action='store_true', help='start a listener')
	parser.add_argument('-s', '--shell', action='store_true', help='start an interactive bind shell')
	parser.add_argument('-v', '--verbose', action='store_true', help='show some connection information')

	# we need at least one argument
	if len(sys.argv) < 2:
		parser.print_help()
		sys.exit(0)

	args = parser.parse_args()
	pc = PyCat(args)
	pc.run()
