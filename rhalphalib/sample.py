import numpy as np
import numbers
from .parameter import NuisanceParameter, DependentParameter, Observable
from .util import _to_numpy, _to_TH1


class Sample(object):
    """
    Sample base class
    """
    SIGNAL, BACKGROUND = range(2)

    def __init__(self, name, sampletype):
        self._name = name
        self._sampletype = sampletype
        self._observable = None

    def __repr__(self):
        return "<%s (%s) instance at 0x%x>" % (
            self.__class__.__name__,
            self._name,
            id(self),
        )

    @property
    def name(self):
        return self._name

    @property
    def sampletype(self):
        return self._sampletype

    @property
    def observable(self):
        if self._observable is None:
            raise RuntimeError("A Sample was not constructed correctly")
        return self._observable

    @observable.setter
    def observable(self, obs):
        # TODO check compatible?
        self._observable = obs

    @property
    def parameters(self):
        raise NotImplementedError

    def normalization(self):
        raise NotImplementedError

    def setParamEffect(self, param, effect_up, effect_down=None):
        raise NotImplementedError

    def getParamEffect(self, param, up=True):
        raise NotImplementedError

    def getExpectation(self, nominal=False):
        raise NotImplementedError

    def renderRoofit(self, workspace):
        raise NotImplementedError

    def combineParamEffect(self, param):
        raise NotImplementedError


class TemplateSample(Sample):
    def __init__(self, name, sampletype, template):
        '''
        name: self-explanatory
        sampletype: Sample.SIGNAL or BACKGROUND or DATA
        template: Either a ROOT TH1, a 1D Coffea Hist object, or a numpy histogram
            in the latter case, please extend the numpy histogram tuple to define an observable name
            i.e. (sumw, binning, name)
            (for the others, the observable name is taken from the x axis name)
        '''
        super(TemplateSample, self).__init__(name, sampletype)
        sumw, binning, obs_name = _to_numpy(template)
        observable = Observable(obs_name, binning)
        self._observable = observable
        self._nominal = sumw
        self._paramEffectsUp = {}
        self._paramEffectsDown = {}

    @property
    def parameters(self):
        '''
        Set of independent parameters that affect this sample
        '''
        return set(self._paramEffectsUp.keys())

    def normalization(self):
        return self._nominal.sum()

    def setParamEffect(self, param, effect_up, effect_down=None):
        '''
        Set the effect of a parameter on a sample (e.g. the size of unc. or multiplier for shape unc.)
        param: a Parameter object
        effect_up: a numpy array representing the relative (multiplicative) effect of the parameter on the bin yields,
                   or a single number representing the relative effect on the sample normalization,
                   or a histogram representing the *bin yield* under the effect of the parameter (i.e. not relative)
        effect_down: if asymmetric effects, fill this in, otherwise the effect_up value will be symmetrized

        N.B. the parameter must have a compatible combinePrior, i.e. if param.combinePrior is 'shape', then one must pass a numpy array
        '''
        if not isinstance(param, NuisanceParameter):
            raise ValueError("Template morphing can only be done via independent parameters with priors (i.e. a NuisanceParameter)")

        if isinstance(effect_up, np.ndarray):
            if len(effect_up) != self.observable.nbins:
                raise ValueError("effect_up has the wrong number of bins (%d, expected %d)" % (len(effect_up), self.observable.nbins))
        elif isinstance(effect_up, numbers.Number):
            if 'shape' in param.combinePrior:
                effect_up = np.full(self.observable.nbins, effect_up)
        else:
            effect_up, binning, _ = _to_numpy(effect_up)
            if not np.array_equal(binning, self.observable.binning):
                raise ValueError("effect_up has incompatible binning with sample %r" % self)
            zerobins = self._nominal <= 0.
            effect_up[zerobins] = 0.
            effect_up[~zerobins] /= self._nominal[~zerobins]
        self._paramEffectsUp[param] = effect_up

        if effect_down is not None:
            if isinstance(effect_down, np.ndarray):
                if len(effect_down) != self.observable.nbins:
                    raise ValueError("effect_down has the wrong number of bins (%d, expected %d)" % (len(effect_down), self.observable.nbins))
            elif isinstance(effect_down, numbers.Number):
                if 'shape' in param.combinePrior:
                    effect_down = np.full(self.observable.nbins, effect_down)
            else:
                effect_down, binning, _ = _to_numpy(effect_down)
                if not np.array_equal(binning, self.observable.binning):
                    raise ValueError("effect_down has incompatible binning with sample %r" % self)
                zerobins = self._nominal <= 0.
                effect_down[zerobins] = 0.
                effect_down[~zerobins] /= self._nominal[~zerobins]
            self._paramEffectsDown[param] = effect_down
        else:
            self._paramEffectsDown[param] = None

    def getParamEffect(self, param, up=True):
        '''
        Get the parameter effect
        '''
        if up:
            return self._paramEffectsUp[param]
        else:
            if self._paramEffectsDown[param] is None:
                # TODO the symmeterized value depends on if param prior is 'shapeN' or 'shape'
                return 1. / self._paramEffectsUp[param]
            return self._paramEffectsDown[param]

    def getExpectation(self, nominal=False):
        '''
        Create an array of per-bin expectations, accounting for all nuisance parameter effects
            nominal: if True, calculate the nominal expectation (i.e. just plain numbers)
        '''
        if nominal:
            return self._nominal
        else:
            # TODO: construct a DependentParameter per bin, as a function of the nuisance params
            raise NotImplementedError

    def renderRoofit(self, workspace):
        '''
        Import the necessary Roofit objects into the workspace for this sample
        and return an extended pdf representing this sample's prediciton for pdf and norm.
        '''
        import ROOT
        rooObservable = self.observable.renderRoofit(workspace)
        rooTemplate = ROOT.RooDataHist(self.name, self.name, ROOT.RooArgList(rooObservable), _to_TH1(self._nominal, self.observable.binning, self.observable.name))
        workspace.add(rooTemplate)
        for param in self.parameters:
            effect_up = self.getParamEffect(param, up=True)
            if not isinstance(effect_up, np.ndarray):
                # Normalization systematics can just go into combine datacards
                continue
            name = self.name + '_' + param.name + 'Up'
            shape = self._nominal * effect_up
            rooTemplate = ROOT.RooDataHist(name, name, ROOT.RooArgList(rooObservable), _to_TH1(shape, self.observable.binning, self.observable.name))
            workspace.add(rooTemplate)
            name = self.name + '_' + param.name + 'Down'
            shape = self._nominal * self.getParamEffect(param, up=False)
            rooTemplate = ROOT.RooDataHist(name, name, ROOT.RooArgList(rooObservable), _to_TH1(shape, self.observable.binning, self.observable.name))
            workspace.add(rooTemplate)

        # TODO build the pdf from the data hist, maybe or maybe not with systematics, return pdf and normalization
        return None, None

    def combineParamEffect(self, param):
        '''
        A formatted string for placement into the combine datacard that represents
        the effect of a parameter on a sample (e.g. the size of unc. or multiplier for shape unc.)
        '''
        if param not in self._paramEffectsUp:
            return '-'
        elif 'shape' in param.combinePrior:
            return '1'
        else:
            up = self._paramEffectsUp[param]
            down = self._paramEffectsDown[param]
            if down is None:
                return '%.3f' % up
            else:
                return '%.3f/%.3f' % (up, down)


class ParametericSample(Sample):
    UseRooParametricHist = False

    def __init__(self, name, sampletype, observable, params):
        '''
        Create a sample that is a binned function, where each bin yield
        is given by the param in params.  The list params should have the
        same number of bins as observable.
        '''
        super(ParametericSample, self).__init__(name, sampletype)
        if not isinstance(observable, Observable):
            raise ValueError
        if len(params) != observable.nbins:
            raise ValueError
        self._observable = observable
        self._params = np.array(params)
        self._paramEffectsUp = {}
        self._paramEffectsDown = {}

    @property
    def parameters(self):
        '''
        Set of independent parameters that affect this sample
        '''
        pset = set()
        for p in self._params:
            pset.update(p.getDependents(deep=True))
        pset.update(self._paramEffectsUp.keys())
        return pset

    def normalization(self):
        '''
        For combine, the normalization in the card is used to scale the parameteric process PDF
        Since we provide an explicit normalization function, this should always stay at 1.
        '''
        return 1.

    def setParamEffect(self, param, effect_up, effect_down=None):
        '''
        Set the effect of a parameter on a sample (e.g. the size of unc. or multiplier for shape unc.)
        param: a Parameter object
        effect_up: a numpy array representing the multiplicative effect of the parameter on the yield, or a single number
        effect_down: if asymmetric effects, fill this in, otherwise the effect_up value will be symmetrized

        For ParametericSample, only relative effects are supported.  Not sure if they are useful though.
        '''
        raise NotImplementedError

    def getParamEffect(self, param, up=True):
        '''
        Get the parameter effect
        '''
        raise NotImplementedError

    def getExpectation(self, nominal=False):
        '''
        Create an array of per-bin expectations, accounting for all nuisance parameter effects
            nominal: if True, calculate the nominal expectation (i.e. just plain numbers)
        '''
        params = self._params
        if nominal:
            return np.array([p.value for p in params])
        else:
            # TODO: create morph/modifier of self._params with any additional effects in _paramEffectsUp/Down
            for i, p in enumerate(params):
                p.name = self.name + '_bin%d' % i
                if isinstance(p, DependentParameter):
                    # Let's make sure to render these
                    p.intermediate = False
            return params

    def renderRoofit(self, workspace):
        '''
        Produce a RooParametricHist and add to workspace
        '''
        import ROOT
        rooObservable = self.observable.renderRoofit(workspace)
        params = self.getExpectation()

        if self.UseRooParametricHist:
            rooParams = [p.renderRoofit(workspace) for p in params]
            # need a dummy hist to generate proper binning
            dummyHist = _to_TH1(np.zeros(len(self._params)), self.observable.binning, self.observable.name)
            rooTemplate = ROOT.RooParametricHist(self.name, self.name, rooObservable, ROOT.RooArgList.fromiter(rooParams), dummyHist)
            rooNorm = ROOT.RooAddition(self.name + '_norm', self.name + '_norm', ROOT.RooArgList.fromiter(rooParams))
            workspace.add(rooTemplate)
            workspace.add(rooNorm)
        else:
            # RooParametricStepFunction expects parameters to represent PDF density (i.e. bin width normalized, and integrates to 1)
            norm = params.sum()
            norm.name = self.name + '_norm'
            norm.intermediate = False

            binw = np.diff(self.observable.binning)
            dparams = params / binw / norm

            for p, oldp in zip(dparams, params):
                p.name = oldp.name + "_density"
                p.intermediate = False

            # The last bin value is defined by 1 - sum(others), so no need to render it
            rooParams = [p.renderRoofit(workspace) for p in dparams[:-1]]
            rooTemplate = ROOT.RooParametricStepFunction(self.name, self.name,
                                                         rooObservable,
                                                         ROOT.RooArgList.fromiter(rooParams),
                                                         self.observable.binningTArrayD(),
                                                         self.observable.nbins
                                                         )
            workspace.add(rooTemplate)
            rooNorm = norm.renderRoofit(workspace)  # already rendered but we want to return it
        return rooTemplate, rooNorm

    def combineParamEffect(self, param):
        '''
        Combine cannot build shape param effects for parameterized templates, so we have to do it in the model
        For normalization effects, I am not sure what happens.. if combine adds the nuisance properly then we just
        need the effect size line as below, and we correspondingly should ignore it when calculating effects ourselves.
        This would be annoying though, because then getExpectation() needs to behave different between combine rendering and otherwise.
        '''
        if param not in self._paramEffectsUp:
            return '-'
        elif 'shape' in param.combinePrior:
            return '1'
        else:
            up = self._paramEffectsUp[param]
            down = self._paramEffectsDown[param]
            return '%.3f/%.3f' % (up, down)


class TransferFactorSample(ParametericSample):
    def __init__(self, name, sampletype, transferfactor, dependentsample, observable=None):
        '''
        Create a sample that depends on another Sample by some transfer factor.
        The transfor factor can be a constant, an array of parameters of same length
        as the dependent sample binning, or a matrix of parameters where the second
        dimension matches the sample binning, i.e. expectation = tf @ dependent_expectation.
        The latter requires an additional observable argument to specify the definition of the first dimension.
        In all cases, please use numpy object arrays of Parameter types.
        '''
        if not isinstance(transferfactor, np.ndarray):
            raise ValueError("Transfer factor is not a numpy array")
        if not isinstance(dependentsample, Sample):
            raise ValueError("Dependent sample does not inherit from Sample")
        if len(transferfactor.shape) == 2:
            if observable is None:
                raise ValueError("Transfer factor is 2D array, please provide an observable")
            params = np.dot(transferfactor, dependentsample.getExpectation())
        elif len(transferfactor.shape) <= 1:
            observable = dependentsample.observable
            params = transferfactor * dependentsample.getExpectation()
        else:
            raise ValueError("Transfer factor has invalid dimension")
        super(TransferFactorSample, self).__init__(name, sampletype, observable, params)
        self._transferfactor = transferfactor
        self._dependentsample = dependentsample

    @property
    def parameters(self):
        '''
        Set of independent parameters that affect this sample
        '''
        pset = set()
        for p in self._transferfactor:
            pset.update(p.getDependents(deep=True))
        pset.update(self._dependentsample.parameters)
        return pset
