#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

"""
Contains the projectq interface to a C++-based simulator, which has to be
built first. If the c++ simulator is not exported to python, a (slow) python
implementation is used as an alternative.
"""

import random
from projectq.cengines import BasicEngine
from projectq.meta import get_control_count
from projectq.ops import (NOT,
                          H,
                          R,
                          Measure,
                          FlushGate,
                          Allocate,
                          Deallocate,
                          BasicMathGate)

try:
	from ._cppsim import Simulator as SimulatorBackend
except ImportError:
	from ._pysim import Simulator as SimulatorBackend


class Simulator(BasicEngine):
	"""
	Simulator is a compiler engine which simulates a quantum computer using C++-
	based kernels.
	
	OpenMP is enabled and the number of threads can be controlled using the
	OMP_NUM_THREADS environment variable, i.e.
	
	.. code-block:: bash
	
		export OMP_NUM_THREADS=4 # use 4 threads
		export OMP_PROC_BIND=spread # bind threads to processors by spreading
	"""
	def __init__(self, gate_fusion=False, rnd_seed=None):
		"""
		Construct the C++/Python-simulator object and initialize it with a random
		seed.
		
		Args:
			gate_fusion (bool): If True, gates are cached and only executed once a
				certain gate-size has been reached (only has an effect for the c++
				simulator).
			rnd_seed (int): Random seed (uses random.randint(0, 1024) by default).
			
		Example of gate_fusion: Instead of applying a Hadamard gate to 5 qubits,
		the simulator calculates the kronecker product of the 1-qubit matrices and
		then applies 1 5-qubit gate. This increases operational intensity and
		keeps the simulator from having to iterate through the state vector
		multiple times. Depending on the system (and, especially, number of
		threads), this may or may not be beneficial.
		
		Note:
			If the C++ Simulator extension was not built or cannot be found, the
			Simulator defaults to a Python implementation of the kernels. While this
			is much slower, it is still good enough to run basic quantum algorithms.
			
			If you need to run large simulations, check out the tutorial in the docs
			which gives futher hints on how to build the C++ extension.
		"""
		if rnd_seed is None:
			rnd_seed = random.randint(0, 1024)
		BasicEngine.__init__(self)
		self._simulator = SimulatorBackend(rnd_seed)
		self._gate_fusion = gate_fusion
	
	def is_available(self, cmd):
		"""
		Specialized implementation of is_available: The simulator can deal with
		all arbitrarily-controlled single-qubit gates which provide a gate-matrix
		(via gate.get_matrix()).
		
		Args:
			cmd (Command): Command for which to check availability (single-qubit
				gate, arbitrary controls)
			
		Returns:
			True if it can be simulated and False otherwise.
		"""
		if (cmd.gate == Measure or cmd.gate == Allocate or cmd.gate == Deallocate
		    or isinstance(cmd.gate, BasicMathGate)):
			return True
		try:
			m = cmd.gate.matrix
			if len(m) > 2:
				return False
			return True
		except:
			return False
	
	def cheat(self):
		"""
		Access the ordering of the qubits and the state vector directly.
		
		This is a cheat function which enables, e.g., more efficient evaluation of
		expectation values and debugging.
		
		Returns:
			A tuple where the first entry is a dictionary mapping qubit indices to
			bit-locations and the second entry is the corresponding state vector
		"""
		return self._simulator.cheat()
	
	def _handle(self, cmd):
		"""
		Handle all commands, i.e., call the member functions of the C++-simulator
		object corresponding to measurement, allocation/deallocation, and
		(controlled) single-qubit gate.
		
		Args:
			cmd (Command): Command to handle.
		
		Raises:
			Exception: If a non-single-qubit gate needs to be processed (which
			should never happen due to is_available).
		"""
		#print(cmd)
		if cmd.gate == Measure:
			assert(get_control_count(cmd) == 0)
			ids = [qb.id for qr in cmd.qubits for qb in qr]
			out = self._simulator.measure_qubits(ids)
			i = 0
			for qr in cmd.qubits:
				for qb in qr:
					self.main_engine.set_measurement_result(qb, out[i])
					i += 1
		elif cmd.gate == Allocate:
			ID = cmd.qubits[0][0].id
			self._simulator.allocate_qubit(ID)
		elif cmd.gate == Deallocate:
			ID = cmd.qubits[0][0].id
			self._simulator.deallocate_qubit(ID)
		elif isinstance(cmd.gate, BasicMathGate):
			qubitids = []
			for qr in cmd.qubits:
				qubitids.append([])
				for qb in qr:
					qubitids[-1].append(qb.id)
			self._simulator.emulate_math(cmd.gate.get_math_function(cmd.qubits),
			                             qubitids, [qb.id for
			                                        qb in cmd.control_qubits])
		elif len(cmd.gate.matrix) == 2:
			matrix = cmd.gate.matrix
			self._simulator.apply_controlled_gate(matrix.tolist(),
			                                      [cmd.qubits[0][0].id],
			                                      [qb.id for qb in
			                                      cmd.control_qubits])
			if not self._gate_fusion:
				self._simulator.run()
		else:
			raise Exception("This simulator only supports controlled single-qubit "
			                "gates!\nPlease add an auto-replacer engine to your "
			                "list of compiler engines.")
	
	def receive(self, command_list):
		"""
		Receive a list of commands from the previous engine and handle them
		(simulate them classically) prior to sending them on to the next engine.
		
		Args:
			command_list (list<Command>): List of commands to execute on the
				simulator.
		"""
		for cmd in command_list:
			if not cmd.gate == FlushGate():
				self._handle(cmd)
			else:
				self._simulator.run()  # flush gate --> run all saved gates
			if not self.is_last_engine:
				self.send([cmd])
