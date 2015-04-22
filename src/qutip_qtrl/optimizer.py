# -*- coding: utf-8 -*-
# This file is part of QuTiP: Quantum Toolbox in Python.
#
#    Copyright (c) 2014 and later, Alexander J G Pitchford
#    All rights reserved.
#
#    Redistribution and use in source and binary forms, with or without
#    modification, are permitted provided that the following conditions are
#    met:
#
#    1. Redistributions of source code must retain the above copyright notice,
#       this list of conditions and the following disclaimer.
#
#    2. Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#
#    3. Neither the name of the QuTiP: Quantum Toolbox in Python nor the names
#       of its contributors may be used to endorse or promote products derived
#       from this software without specific prior written permission.
#
#    THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
#    "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
#    LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
#    PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
#    HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
#    SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
#    LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
#    DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
#    THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
#    (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
#    OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
###############################################################################

# @author: Alexander Pitchford
# @email1: agp1@aber.ac.uk
# @email2: alex.pitchford@gmail.com
# @organization: Aberystwyth University
# @supervisor: Daniel Burgarth

"""
Classes here are expected to implement a run_optimization function
that will use some method for optimising the control pulse, as defined
by the control amplitudes. The system that the pulse acts upon are defined
by the Dynamics object that must be passed in the instantiation.

The methods are typically N dimensional function optimisers that
find the minima of a fidelity error function. Note the number of variables
for the fidelity function is the number of control timeslots,
i.e. n_ctrls x Ntimeslots
The methods will call functions on the Dynamics.fid_computer object,
one or many times per interation,
to get the fidelity error and gradient wrt to the amplitudes.
The optimisation will stop when one of the termination conditions are met,
for example: the fidelity aim has be reached, a local minima has been found,
the maximum time allowed has been exceeded

These function optimisation methods are so far from SciPy.optimize
The two methods implemented are:
    BFGS - Broyden–Fletcher–Goldfarb–Shanno algorithm
        This a quasi second order Newton method. It uses successive calls to
        the gradient function to make an estimation of the curvature (Hessian)
        and hence direct its search for the function minima
        The SciPy implementation is pure Python and hance is execution speed is
        not high
        use subclass: OptimizerBFGS

    L-BFGS-B - Bounded, limited memory BFGS
        This a version of the BFGS method where the Hessian approximation is
        only based on a set of the most recent gradient calls. It generally
        performs better where the are a large number of variables
        The SciPy implementation of L-BFGS-B is wrapper around a well
        established and actively maintained implementation in Fortran
        Its is therefore very fast.
        # See SciPy documentation for credit and details on the
        # scipy.optimize.fmin_l_bfgs_b function
        use subclass: OptimizerLBFGSB

The baseclass Optimizer implements the function wrappers to the
fidelity error, gradient, and iteration callback functions.
These are called from the within the SciPy optimisation functions.
The subclasses implement the algorithm specific pulse optimisation function.
"""

import os
import numpy as np
import timeit
import scipy.optimize as spopt
import copy
import collections
# QuTiP
from qutip import Qobj
import qutip.logging as logging
logger = logging.get_logger()
# QuTiP control modules
import qutip.control.optimresult as optimresult
import qutip.control.termcond as termcond
import qutip.control.errors as errors
import qutip.control.dynamics as dynamics
import qutip.control.pulsegen as pulsegen

class Optimizer:
    """
    Base class for all control pulse optimisers. This class should not be
    instantiated, use its subclasses
    This class implements the fidelity, gradient and interation callback
    functions.
    All subclass objects must be initialised with a
        OptimConfig instance - various configuration options
        Dynamics instance - describes the dynamics of the (quantum) system
                            to be control optimised

    Attributes
    ----------
    log_level : integer
        level of messaging output from the logger.
        Options are attributes of qutip.logging,
        in decreasing levels of messaging, are:
        DEBUG_INTENSE, DEBUG_VERBOSE, DEBUG, INFO, WARN, ERROR, CRITICAL
        Anything WARN or above is effectively 'quiet' execution,
        assuming everything runs as expected.
        The default NOTSET implies that the level will be taken from
        the QuTiP settings file, which by default is WARN
        Note value should be set using set_log_level

    alg : string
        Algorithm to use in pulse optimisation.
        Options are:
            'GRAPE' (default) - GRadient Ascent Pulse Engineering
            'CRAB' - Chopped RAndom Basis
            
    alg_params : Dictionary
        options that are specific to the pulse optim algorithm 
        that is GRAPE or CRAB
        
    disp_conv_msg : bool
        Set true to display a convergence message 
        (for scipy.optimize.minimize methods anyway)
        
    optim_method : string
        a scipy.optimize.minimize method that will be used to optimise
        the pulse for minimum fidelity error
        
    method_params : Dictionary
        Options for the optim_method. 
        Note that where there is an equivalent attribute of this instance
        or the termination_conditions (for example maxiter)
        it will override an value in these options

    amp_lbound : float or list of floats
        lower boundaries for the control amplitudes
        Can be a scalar value applied to all controls
        or a list of bounds for each control

    amp_ubound : float or list of floats
        upper boundaries for the control amplitudes
        Can be a scalar value applied to all controls
        or a list of bounds for each control
        
    bounds : List of floats
        Bounds for the parameters.
        If not set before the run_optimization call then the list
        is built automatically based on the amp_lbound and amp_ubound
        attributes.
        Setting this attribute directly allows specific bounds to be set
        for individual parameters.
        Note: Only some methods use bounds

    dynamics : Dynamics (subclass instance)
        describes the dynamics of the (quantum) system to be control optimised
        (see Dynamics classes for details)

    config : OptimConfig instance
        various configuration options
        (see OptimConfig for details)

    termination_conditions : TerminationCondition instance
        attributes determine when the optimisation will end

    pulse_generator : PulseGen (subclass instance)
        (can be) used to create initial pulses
        not used by the class, but set by pulseoptim.create_pulse_optimizer

    stats : Stats
        attributes of which give performance stats for the optimisation
        set to None to reduce overhead of calculating stats.
        Note it is (usually) shared with the Dynamics instance
    """

    def __init__(self, config, dyn):
        self.dynamics = dyn
        self.config = config
        self.reset()
        dyn.parent = self

    def reset(self):
        self.set_log_level(self.config.log_level)
        self.id_text = 'OPTIM_GRAPE'
        self.termination_conditions = None
        self.pulse_generator = None
        self.disp_conv_msg = False
        self.iteration_steps = None
        self.record_iteration_steps=False
        self.alg_params = None
        self.method = 'Nelder-mead'
        self.method_params = None
        self.method_options = None
        self.amp_lbound = None
        self.amp_ubound = None
        self.bounds = None
        self.num_iter = 0
        self.num_fid_func_calls = 0
        self.stats = None
        self.wall_time_optim_start = 0.0

        # test out file paths
        self._iter_fpath = None
        self._fid_err_fpath = None
        self._grad_norm_fpath = None

        # AJGP 2015-04-21: 
        # These (copying from config) are here for backward compatibility
        if hasattr(self.config, 'amp_lbound'):
            if self.config.amp_lbound:
                self.amp_lbound = self.config.amp_lbound
        if hasattr(self.config, 'amp_ubound'):
            if self.config.amp_ubound:
                self.amp_ubound = self.config.amp_ubound

    def set_log_level(self, lvl):
        """
        Set the log_level attribute and set the level of the logger
        that is call logger.setLevel(lvl)
        """
        self.log_level = lvl
        logger.setLevel(lvl)

#    def run_optimization(self, term_conds=None):
#        """
#        Run the pulse optimisation algorithm
#
#        This class does not implement a method for running the optimisation
#        A subclass should be used, e.g. OptimizerLBFGSB
#
#        If the parameter term_conds=None, then the termination_conditions
#        attribute must already be set. It will be overwritten if the
#        parameter is not None
#        """
#        raise errors.UsageError(
#            "No method defined for running optimisation."
#            " Suspect base class was used where sub class should have been")
   
    def _create_result(self):
        """
        create the result object
        and set the initial_amps attribute as the current amplitudes
        """
        result = optimresult.OptimResult()
        result.initial_fid_err = self.dynamics.fid_computer.get_fid_err()
        result.initial_amps = self.dynamics.ctrl_amps.copy()
        result.time = self.dynamics.time
        return result

    def init_optim(self, term_conds):
        """
        Check optimiser attribute status and passed parameters before
        running the optimisation.
        This is called by run_optimization, but could called independently
        to check the configuration.
        """

        self._check_prepare_test_out_files()

        if term_conds is not None:
            self.termination_conditions = term_conds
        term_conds = self.termination_conditions
        
        if not isinstance(term_conds, termcond.TerminationConditions):
            raise errors.UsageError("No termination conditions for the "
                                    "optimisation function")

        if not isinstance(self.dynamics, dynamics.Dynamics):
            raise errors.UsageError("No dynamics object attribute set")
        self.dynamics.check_ctrls_initialized()

        self.apply_method_params()
        
        if term_conds.fid_err_targ is None and term_conds.fid_goal is None:
            raise errors.UsageError("Either the goal or the fidelity "
                                    "error tolerance must be set")

        if term_conds.fid_err_targ is None:
            term_conds.fid_err_targ = np.abs(1 - term_conds.fid_goal)

        if term_conds.fid_goal is None:
            term_conds.fid_goal = 1 - term_conds.fid_err_targ

        if self.stats is not None:
            self.stats.clear()
        
        self.num_iter = 1
        self.num_fid_func_calls = 0
        self.iteration_steps = None

    def _check_prepare_test_out_files(self):
        cfg = self.config
        if cfg.any_test_files():
            if cfg.check_create_test_out_dir():
                if self.stats is None:
                    logger.warn("Cannot output test files when stats"
                                " attribute is not set.")
                    cfg.clear_test_out_flags()

        if cfg.any_test_files():
            dyn = self.dynamics
            f_ext = "_{}_{}_{}_{}{}".format(
                self.id_text,
                dyn.id_text,
                dyn.prop_computer.id_text,
                dyn.fid_computer.id_text,
                cfg.test_out_f_ext)
            # Prepare the files that will remain open throughout the run
            if cfg.test_out_iter:
                fname = "iteration_log" + f_ext
                self._iter_fpath = os.path.join(cfg.test_out_dir, fname)
                fh = open(self._iter_fpath, 'w')
                fh.write("iter           wall_time       "
                         "fid_err     grad_norm\n")
                fh.close()
                logger.info("Iteration log will be saved to:\n{}".format(
                    self._iter_fpath))
            else:
                self._iter_fpath = None


            if cfg.test_out_fid_err:
                fname = "fid_err" + f_ext
                self._fid_err_fpath = os.path.join(cfg.test_out_dir, fname)
                fh = open(self._fid_err_fpath, 'w')
                fh.write("call             fid_err\n")
                fh.close()
                logger.info("Fidelity error log will be saved to:\n{}".format(
                    self._fid_err_fpath))
            else:
                self._fid_err_fpath = None

            if cfg.test_out_grad_norm:
                fname = "grad_norm" + f_ext
                self._grad_norm_fpath = os.path.join(cfg.test_out_dir, fname)
                fh = open(self._grad_norm_fpath, 'w')
                fh.write("call           grad_norm\n")
                fh.close()
                logger.info("Gradient norm log will be saved to:\n{}".format(
                    self._grad_norm_fpath))

            else:
                self._grad_norm_fpath = None
                
    def _build_method_options(self):
        """
        Creates the method_options dictionary for the scipy.optimize.minimize
        function based on the attributes of this object and the 
        termination_conditions
        It assumes that apply_method_params has already been run and
        hence the method_options attribute may already contain items.
        These values will NOT be overridden
        """
        tc = self.termination_conditions
        if self.method_options is None:
            self.method_options = {}
        mo = self.method_options
        if tc.max_iterations > 0 and not 'maxiter' in mo:
            mo['maxiter'] = tc.max_iterations
        if tc.max_fid_func_calls > 0 and not 'maxfev' in mo:
            mo['maxfev'] = tc.max_fid_func_calls
        if tc.min_gradient_norm > 0 and not 'gtol' in mo:
            mo['gtol'] = tc.min_gradient_norm
        if not 'disp' in mo:
            mo['disp'] = self.disp_conv_msg
        
        return mo
        
    def apply_method_params(self, params=None):
        """
        Loops through all the method_params 
        (either passed here or the method_params attribute)
        If the name matches an attribute of this object or the
        termination conditions object, then the value of this attribute 
        is set. Otherwise it is assumed to a method_option for the
        scipy.optimize.minimize function
        """
        if not params:
            params = self.method_params
        
        if isinstance(params, dict):
            self.method_params = params
            unused_params = {}
            for key, val in params.iteritems():
                if hasattr(self, key):
                    setattr(self, key, val)
                if hasattr(self.termination_conditions, key):
                    setattr(self.termination_conditions, key, val)
                else:
                    unused_params[key] = val
            
            if len(unused_params) > 0:
                if not isinstance(self.method_options, dict):
                    self.method_options = unused_params
                else:
                    self.method_options.update(unused_params)

        
    def _build_bounds_list(self):
        cfg = self.config
        dyn = self.dynamics
        n_ctrls = dyn.get_num_ctrls()
        self.bounds = []
        for t in range(dyn.num_tslots):
            for c in range(n_ctrls):
                if isinstance(self.amp_lbound, list):
                    lb = self.amp_lbound[c]
                else:
                    lb = self.amp_lbound
                if isinstance(self.amp_ubound, list):
                    ub = self.amp_ubound[c]
                else:
                    ub = self.amp_ubound
                
                if not lb is None and np.isinf(lb):
                    lb = None
                if not ub is None and np.isinf(ub):
                    ub = None
                    
                self.bounds.append((lb, ub))
        
    def run_optimization(self, term_conds=None):
        """
        This default function optimisation method is a wrapper to the
        scipy.optimize.minimize function. 
        
        It will attempt to minimise the fidelity error with respect to some
        parameters, which are determined by _get_optim_var_vals (see below)
        
        The optimisation end when one of the passed termination conditions
        has been met, e.g. target achieved, wall time, or 
        function call or iteration count exceeded. Note these 
        conditions include gradient minimum met (local minima) for
        methods that use a gradient.
        
        The function minimisation method is taken from the optim_method
        attribute. Note that not all of these methods have been tested.
        Note that some of these use a gradient and some do not.
        See the scipy documentation for details. Options specific to the
        method can be passed setting the method_params attribute.

        If the parameter term_conds=None, then the termination_conditions
        attribute must already be set. It will be overwritten if the
        parameter is not None

        The result is returned in an OptimResult object, which includes
        the final fidelity, time evolution, reason for termination etc
        """
        self.init_optim(term_conds)
        term_conds = self.termination_conditions
        dyn = self.dynamics
        cfg = self.config
        self.optim_var_vals = self._get_optim_var_vals()
        st_time = timeit.default_timer()
        self.wall_time_optimize_start = st_time

        if self.stats is not None:
            self.stats.wall_time_optim_start = st_time
            self.stats.wall_time_optim_end = 0.0
            self.stats.num_iter = 1
        
        if self.bounds is None:
            self._build_bounds_list()
        
        self._build_method_options()
        
        result = self._create_result()

        if self.log_level <= logging.INFO:
            logger.info("Optimising pulse using '{}' method".format(
                                    self.method))

        try:
            opt_res = spopt.minimize(
                self.fid_err_func_wrapper, self.optim_var_vals,
                method=self.method,
                jac=self.fid_err_grad_wrapper,
                bounds=self.bounds,
                options=self.method_options,
                callback=self.iter_step_callback_func)

            amps = self._get_ctrl_amps(opt_res.x)
            dyn.update_ctrl_amps(amps)
            result.termination_reason = opt_res.message
            # Note the iterations are counted in this object as well
            # so there are compared here for interest sake only
            if self.num_iter != opt_res.nit:
                logger.info("The number of iterations counted {} "
                            " does not match the number reported {} "
                            "by {}".format(self.num_iter, opt_res.nit, 
                                            self.method))
            result.num_iter = opt_res.nit
            
        except errors.OptimizationTerminate as except_term:
            self._interpret_term_exception(except_term, result)

        end_time = timeit.default_timer()
        self._add_common_result_attribs(result, st_time, end_time)

        return result
        
    def _get_optim_var_vals(self):
        """
        Generate the 1d array that holds the current variable values 
        of the function to be optimised
        By default (as used in GRAPE) these are the control amplitudes 
        in each timeslot
        """
        return self.dynamics.ctrl_amps.reshape([-1])
                
    def _get_ctrl_amps(self, optim_var_vals):
        """
        Get the control amplitudes from the current variable values 
        of the function to be optimised.
        that is the 1d array that is passed from the optimisation method
        Note for GRAPE these are the function optimiser parameters
        (and this is the default)       
        Returns
        -------
        float array[dynamics.num_tslots, dynamics.num_ctrls]
        """
        amps = optim_var_vals.reshape(self.dynamics.ctrl_amps.shape)
            
        return amps

    def fid_err_func_wrapper(self, *args):
        """
        Get the fidelity error achieved using the ctrl amplitudes passed
        in as the first argument.

        This is called by generic optimisation algorithm as the
        func to the minimised. The argument is the current
        variable values, i.e. control amplitudes, passed as
        a flat array. Hence these are reshaped as [nTimeslots, n_ctrls]
        and then used to update the stored ctrl values (if they have changed)

        The error is checked against the target, and the optimisation is
        terminated if the target has been achieved.
        """
        self.num_fid_func_calls += 1
        # *** update stats ***
        if self.stats is not None:
            self.stats.num_fidelity_func_calls = self.num_fid_func_calls
            if self.log_level <= logging.DEBUG:
                logger.debug("fidelity error call {}".format(
                    self.stats.num_fidelity_func_calls))

        amps = self._get_ctrl_amps(args[0].copy())
        self.dynamics.update_ctrl_amps(amps)

        tc = self.termination_conditions
        err = self.dynamics.fid_computer.get_fid_err()

        if self._fid_err_fpath is not None:
            fh = open(self._fid_err_fpath, 'a')
            fh.write("{:<10n}{:14.6g}\n".format(
                self.stats.num_fidelity_func_calls, err))
            fh.close()

        if err <= tc.fid_err_targ:
            raise errors.GoalAchievedTerminate(err)
            
        if self.num_fid_func_calls > tc.max_fid_func_calls:
            raise errors.MaxFidFuncCallTerminate()
            
        return err

    def fid_err_grad_wrapper(self, *args):
        """
        Get the gradient of the fidelity error with respect to all of the
        variables, i.e. the ctrl amplidutes in each timeslot

        This is called by generic optimisation algorithm as the gradients of
        func to the minimised wrt the variables. The argument is the current
        variable values, i.e. control amplitudes, passed as
        a flat array. Hence these are reshaped as [nTimeslots, n_ctrls]
        and then used to update the stored ctrl values (if they have changed)

        Although the optimisation algorithms have a check within them for
        function convergence, i.e. local minima, the sum of the squares
        of the normalised gradient is checked explicitly, and the
        optimisation is terminated if this is below the min_gradient_norm
        condition
        """
        # *** update stats ***
        if self.stats is not None:
            self.stats.num_grad_func_calls += 1
            if self.log_level <= logging.DEBUG:
                logger.debug("gradient call {}".format(
                    self.stats.num_grad_func_calls))
        amps = self._get_ctrl_amps(args[0].copy())
        self.dynamics.update_ctrl_amps(amps)
        fid_comp = self.dynamics.fid_computer
        # gradient_norm_func is a pointer to the function set in the config
        # that returns the normalised gradients
        grad = fid_comp.get_fid_err_gradient()

        if self._grad_norm_fpath is not None:
            fh = open(self._grad_norm_fpath, 'a')
            fh.write("{:<10n}{:14.6g}\n".format(
                self.stats.num_grad_func_calls, fid_comp.grad_norm))
            fh.close()

        if self.config.test_out_grad:
            # save gradients to file
            dyn = self.dynamics
            fname = "grad_{}_{}_{}_{}_call{}{}".format(
                self.id_text,
                dyn.id_text,
                dyn.prop_computer.id_text,
                dyn.fid_computer.id_text,
                self.stats.num_grad_func_calls,
                self.config.test_out_f_ext)

            fpath = os.path.join(self.config.test_out_dir, fname)
            np.savetxt(fpath, grad, fmt='%11.4g')

        tc = self.termination_conditions
        if fid_comp.grad_norm < tc.min_gradient_norm:
            raise errors.GradMinReachedTerminate(fid_comp.grad_norm)
        return grad.flatten()

    def iter_step_callback_func(self, *args):
        """
        Check the elapsed wall time for the optimisation run so far.
        Terminate if this has exceeded the maximum allowed time
        """
        if self.log_level <= logging.DEBUG:
            logger.debug("Iteration callback {}".format(self.num_iter))

        wall_time = timeit.default_timer() - self.wall_time_optimize_start
        if self._iter_fpath is not None:

            # write out: iter wall_time fid_err grad_norm
            fid_comp = self.dynamics.fid_computer
            fh = open(self._iter_fpath, 'a')
            fh.write("{:<10n}{:14.6g}{:14.6g}{:14.6g}\n".format(
                self.num_iter, wall_time,
                fid_comp.fid_err, fid_comp.grad_norm))
            fh.close()

        tc = self.termination_conditions

        if wall_time > tc.max_wall_time:
            raise errors.MaxWallTimeTerminate()

        self.num_iter += 1
        # *** update stats ***
        if self.stats is not None:
            self.stats.num_iter = self.num_iter

    def _interpret_term_exception(self, except_term, result):
        """
        Update the result object based on the exception that occurred
        during the optimisation
        """
        result.termination_reason = except_term.reason
        if isinstance(except_term, errors.GoalAchievedTerminate):
            result.goal_achieved = True
        elif isinstance(except_term, errors.MaxWallTimeTerminate):
            result.wall_time_limit_exceeded = True
        elif isinstance(except_term, errors.GradMinReachedTerminate):
            result.grad_norm_min_reached = True
        elif isinstance(except_term, errors.MaxFidFuncCallTerminate):
            result.max_fid_func_exceeded = True

    def _add_common_result_attribs(self, result, st_time, end_time):
        """
        Update the result object attributes which are common to all
        optimisers and outcomes
        """
        dyn = self.dynamics
        result.num_iter = self.num_iter
        result.num_fid_func_calls = self.num_fid_func_calls
        result.wall_time = end_time - st_time
        result.fid_err = dyn.fid_computer.get_fid_err()
        result.grad_norm_final = dyn.fid_computer.grad_norm
        result.final_amps = dyn.ctrl_amps
        result.evo_full_final = Qobj(dyn.evo_init2t[dyn.num_tslots])
        # *** update stats ***
        if self.stats is not None:
            self.stats.wall_time_optim_end = end_time
            self.stats.calculate()
            result.stats = copy.copy(self.stats)


class OptimizerBFGS(Optimizer):
    """
    Implements the run_optimization method using the BFGS algorithm
    """
    def reset(self):
        Optimizer.reset(self)
        self.id_text = 'BFGS'

    def run_optimization(self, term_conds=None):
        """
        Optimise the control pulse amplitudes to minimise the fidelity error
        using the BFGS (Broyden–Fletcher–Goldfarb–Shanno) algorithm
        The optimisation end when one of the passed termination conditions
        has been met, e.g. target achieved, gradient minimum met
        (local minima), wall time / iteration count exceeded.

        Essentially this is wrapper to the:
        scipy.optimize.fmin_bfgs
        function

        If the parameter term_conds=None, then the termination_conditions
        attribute must already be set. It will be overwritten if the
        parameter is not None

        The result is returned in an OptimResult object, which includes
        the final fidelity, time evolution, reason for termination etc
        """
        self.init_optim(term_conds)
        term_conds = self.termination_conditions
        dyn = self.dynamics
        self.optim_var_vals = self._get_optim_var_vals()
        st_time = timeit.default_timer()
        self.wall_time_optimize_start = st_time

        if self.stats is not None:
            self.stats.wall_time_optim_start = st_time
            self.stats.wall_time_optim_end = 0.0
            self.stats.num_iter = 1

        if self.log_level <= logging.INFO:
            logger.info("Optimising pulse using BFGS")

        result = self._create_result()
        try:
            optim_var_vals, cost, grad, invHess, nFCalls, nGCalls, warn = \
                spopt.fmin_bfgs(self.fid_err_func_wrapper, 
                                self.optim_var_vals,
                                fprime=self.fid_err_grad_wrapper,
                                callback=self.iter_step_callback_func,
                                gtol=term_conds.min_gradient_norm,
                                maxiter=term_conds.max_iterations,
                                full_output=True, disp=True)

            amps = self._get_ctrl_amps(optim_var_vals)
            dyn.update_ctrl_amps(amps)
            if warn == 1:
                result.max_iter_exceeded = True
                result.termination_reason = "Iteration count limit reached"
            elif warn == 2:
                result.grad_norm_min_reached = True
                result.termination_reason = "Gradient normal minimum reached"

        except errors.OptimizationTerminate as except_term:
            self._interpret_term_exception(except_term, result)

        end_time = timeit.default_timer()
        self._add_common_result_attribs(result, st_time, end_time)

        return result


class OptimizerLBFGSB(Optimizer):
    """
    Implements the run_optimization method using the L-BFGS-B algorithm
    
    Attributes
    ----------
    max_metric_corr : integer
        The maximum number of variable metric corrections used to define
        the limited memory matrix. That is the number of previous
        gradient values that are used to approximate the Hessian
        see the scipy.optimize.fmin_l_bfgs_b documentation for description
        of m argument

    accuracy_factor : float
        Determines the accuracy of the result.
        Typical values for accuracy_factor are: 1e12 for low accuracy;
        1e7 for moderate accuracy; 10.0 for extremely high accuracy
        scipy.optimize.fmin_l_bfgs_b factr argument.
    """

    def reset(self):
        Optimizer.reset(self)
        self.id_text = 'LBFGSB'
        self.max_metric_corr = 10
        self.accuracy_factor = 1e7
        
        # AJGP 2015-04-21: 
        # These (copying from config) are here for backward compatibility
        if hasattr(self.config, 'max_metric_corr'):
            if self.config.max_metric_corr:
                self.max_metric_corr = self.config.max_metric_corr
        if hasattr(self.config, 'accuracy_factor'):
            if self.config.accuracy_factor:
                self.accuracy_factor = self.config.accuracy_factor
        
    def run_optimization(self, term_conds=None):
        """
        Optimise the control pulse amplitudes to minimise the fidelity error
        using the L-BFGS-B algorithm, which is the constrained
        (bounded amplitude values), limited memory, version of the
        Broyden–Fletcher–Goldfarb–Shanno algorithm.

        The optimisation end when one of the passed termination conditions
        has been met, e.g. target achieved, gradient minimum met
        (local minima), wall time / iteration count exceeded.

        Essentially this is wrapper to the:
        scipy.optimize.fmin_l_bfgs_b function
        This in turn is a warpper for well established implementation of
        the L-BFGS-B algorithm written in Fortran, which is therefore
        very fast. See SciPy documentation for credit and details on
        this function.

        If the parameter term_conds=None, then the termination_conditions
        attribute must already be set. It will be overwritten if the
        parameter is not None

        The result is returned in an OptimResult object, which includes
        the final fidelity, time evolution, reason for termination etc
        """
        self.init_optim(term_conds)
        term_conds = self.termination_conditions
        dyn = self.dynamics
        cfg = self.config
        self.optim_var_vals = self._get_optim_var_vals()
        st_time = timeit.default_timer()
        self.wall_time_optimize_start = st_time

        if self.stats is not None:
            self.stats.wall_time_optim_start = st_time
            self.stats.wall_time_optim_end = 0.0
            self.stats.num_iter = 1

        bounds = self._build_bounds_list()
        result = self._create_result()
        if self.log_level < logging.DEBUG:
            alg_msg_lvl = 1
        elif self.log_level == logging.DEBUG:
            alg_msg_lvl = 0
        else:
            alg_msg_lvl = -1

        if self.log_level <= logging.INFO:
            logger.info("Optimising pulse using L-BFGS-B")

        try:
            optim_var_vals, fid, res_dict = spopt.fmin_l_bfgs_b(
                self.fid_err_func_wrapper, self.optim_var_vals,
                fprime=self.fid_err_grad_wrapper,
                callback=self.iter_step_callback_func,
                bounds=bounds,
                m=self.max_metric_corr,
                factr=self.accuracy_factor,
                pgtol=term_conds.min_gradient_norm,
                iprint=alg_msg_lvl,
                maxfun=term_conds.max_fid_func_calls,
                maxiter=term_conds.max_iterations)

            amps = self._get_ctrl_amps(optim_var_vals)
            dyn.update_ctrl_amps(amps)
            warn = res_dict['warnflag']
            if warn == 0:
                result.grad_norm_min_reached = True
                result.termination_reason = "function converged"
            elif warn == 1:
                result.max_iter_exceeded = True
                result.termination_reason = ("Iteration or fidelity "
                                             "function call limit reached")
            elif warn == 2:
                result.termination_reason = res_dict['task']

            result.num_iter = res_dict['nit']
        except errors.OptimizationTerminate as except_term:
            self._interpret_term_exception(except_term, result)

        end_time = timeit.default_timer()
        self._add_common_result_attribs(result, st_time, end_time)

        return result
        
class OptimizerCrab(Optimizer):
    """
    Optimises the pulse using the CRAB algorithm [1].
    It uses the scipy.optimize.minimize function with the method specified
    by the optim_method attribute. See Optimizer.run_optimization for details
    It minimises the fidelity error function with respect to the CRAB
    basis function coefficients.
    
    AJGP ToDo: Add citation here 
    """

    def reset(self):
        Optimizer.reset(self)
        self.id_text = 'CRAB'
        self.num_optim_vars = 0
        
    def init_optim(self, term_conds):
        """
        Check optimiser attribute status and passed parameters before
        running the optimisation.
        This is called by run_optimization, but could called independently
        to check the configuration.
        """
        Optimizer.init_optim(self, term_conds)
        dyn = self.dynamics
        
        self.num_optim_vars = 0
        pulse_gen_valid = True
        # check the pulse generators match the ctrls
        # (in terms of number)
        # and count the number of parameters
        if self.pulse_generator is None:
            pulse_gen_valid = False
            err_msg = "pulse_generator attribute is None"
        elif not isinstance(self.pulse_generator, collections.Iterable):
            pulse_gen_valid = False
            err_msg = "pulse_generator is not iterable"
        
        elif len(self.pulse_generator) != dyn.get_num_ctrls():
            pulse_gen_valid = False
            err_msg = ("the number of pulse generators {} does not equal "
                        "the number of controls {}".format(
                        len(self.pulse_generator), dyn.num_ctrls))
                        
        if pulse_gen_valid:
            for p_gen in self.pulse_generator:
                if not isinstance(p_gen, pulsegen.PulseGenCrab):
                    pulse_gen_valid = False
                    err_msg = (
                        "pulse_generator contained object of type '{}'".format(
                        p_gen.__class__.__name__))
                    break
                self.num_optim_vars += p_gen.num_optim_vars
        
        if not pulse_gen_valid:
            raise errors.UsageError(
                "The pulse_generator attribute must be set to a list of "
                "PulseGenCrab - one for each control. Here " + err_msg)
        
    def _build_bounds_list(self):
        """
        No bounds necessary here, as the bounds for the CRAB parameters
        do not have much physical meaning.
        This needs to override the default method, otherwise the shape
        will be wrong
        """
        return None

    def _get_optim_var_vals(self):
        """
        Generate the 1d array that holds the current variable values 
        of the function to be optimised
        For CRAB these are the basis coefficients
        Returns
        -------
        ndarray (1d) of float
        """
        pvals = []
        for pgen in self.pulse_generator:
            pvals.extend(pgen.get_optim_var_vals())
            
        return np.array(pvals)
                
    def _get_ctrl_amps(self, optim_var_vals):
        """
        Get the control amplitudes from the current variable values 
        of the function to be optimised.
        that is the 1d array that is passed from the optimisation method
        For CRAB the amplitudes will need to calculated by expanding the 
        series        
        
        Returns
        -------
        float array[dynamics.num_tslots, dynamics.num_ctrls]
        """
        dyn = self.dynamics
        
        if self.log_level <= logging.DEBUG:
            changed_params = self.optim_var_vals != optim_var_vals
            logger.debug(
                "{} out of {} optimisation parameters changed".format(
                    changed_params.sum(), len(optim_var_vals)))
        
        amps = np.empty([dyn.num_tslots, dyn.num_ctrls])
        j = 0
        param_idx_st = 0
        for p_gen in self.pulse_generator:
            param_idx_end = param_idx_st + p_gen.num_optim_vars
            pg_pvals = optim_var_vals[param_idx_st:param_idx_end]
            p_gen.set_optim_var_vals(pg_pvals)
            amps[:, j] = p_gen.gen_pulse()
            param_idx_st = param_idx_end
            j += 1
        
        #print("param_idx_end={}".format(param_idx_end))
        self.optim_var_vals = optim_var_vals
        return amps
        
class OptimizerCrabFmin(OptimizerCrab):
    """
    Optimises the pulse using the CRAB algorithm [1].
    It uses the scipy.optimize.fmin function which is effectively a wrapper
    for the Nelder-mead method.
    It minimises the fidelity error function with respect to the CRAB
    basis function coefficients.
    This is the default Optimizer for CRAB.
    AJGP ToDo: Add citation here
    """
    
    def reset(self):
        OptimizerCrab.reset(self)
        self.id_text = 'CRAB_FMIN'
        self.xtol = 1e-4
        self.ftol = 1e-4

    def run_optimization(self, term_conds=None):
        """
        This function optimisation method is a wrapper to the
        scipy.optimize.fmin function. 
        
        It will attempt to minimise the fidelity error with respect to some
        parameters, which are determined by _get_optim_var_vals which
        in the case of CRAB are the basis function coefficients
        
        The optimisation end when one of the passed termination conditions
        has been met, e.g. target achieved, wall time, or
        function call or iteration count exceeded. Specifically to the fmin
        method, the optimisation will stop when change parameter values 
        is less than xtol or the change in function value is below ftol.
        
        If the parameter term_conds=None, then the termination_conditions
        attribute must already be set. It will be overwritten if the
        parameter is not None

        The result is returned in an OptimResult object, which includes
        the final fidelity, time evolution, reason for termination etc
        """
        self.init_optim(term_conds)
        term_conds = self.termination_conditions
        dyn = self.dynamics
        cfg = self.config
        self.optim_var_vals = self._get_optim_var_vals()
        #print("Initial values:\n{}".format(self.optim_var_vals))
        st_time = timeit.default_timer()
        self.wall_time_optimize_start = st_time

        if self.stats is not None:
            self.stats.wall_time_optim_start = st_time
            self.stats.wall_time_optim_end = 0.0
            self.stats.num_iter = 1
                       
        result = self._create_result()

        if self.log_level <= logging.INFO:
            logger.info("Optimising pulse using '{}' method".format(
                                    self.method))

        try:
            ret = spopt.fmin(
                    self.fid_err_func_wrapper, self.optim_var_vals,
                    xtol=self.xtol, ftol=self.ftol,
                    maxiter=term_conds.max_iterations,
                    maxfun=term_conds.max_fid_func_calls,
                    full_output=True, disp=self.disp_conv_msg,
                    retall=self.record_iteration_steps,
                    callback=self.iter_step_callback_func)
            
            final_param_vals = ret[0]
            num_iter = ret[2]
            warn_flag = ret[4]
            if self.record_iteration_steps:
                self.iteration_steps = ret[5]
            amps = self._get_ctrl_amps(final_param_vals)
            dyn.update_ctrl_amps(amps)

            # Note the iterations are counted in this object as well
            # so there are compared here for interest sake only
            if self.num_iter != num_iter:
                logger.info("The number of iterations counted {} "
                            " does not match the number reported {} "
                            "by {}".format(self.num_iter, num_iter, 
                                            self.method))
            result.num_iter = num_iter
            if warn_flag == 0:
                result.termination_reason = \
                    "Function converged (within tolerance)"
            elif warn_flag == 1:
                result.termination_reason = \
                    "Maximum number of function evaluations reached"
                result.max_fid_func_exceeded = True
            elif warn_flag == 2:
                result.termination_reason = \
                    "Maximum number of iterations reached"
                result.max_iter_exceeded = True
            else:
                result.termination_reason = \
                    "Unknown (warn_flag={})".format(warn_flag)
                
        except errors.OptimizationTerminate as except_term:
            self._interpret_term_exception(except_term, result)

        end_time = timeit.default_timer()
        self._add_common_result_attribs(result, st_time, end_time)

        return result
        