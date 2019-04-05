from typing import Callable

import torch
import torch.nn as nn

from typing import Tuple, Union
from torch.distributions import Normal, Categorical
from models.initializer import init_linear_weights_xavier

class MLP(nn.Module):
    """Baseline of Multi-layer perceptron"""

    def __init__(
        self,
        input_size: int,
        hidden_sizes: list,
        output_size: int = 0,
        output_activation: Callable = nn.Sequential(), # identity
        hidden_activation: Callable = nn.ReLU(),
    ):
        """Initialization with xavier

        Args:
            input_size (int): size of input layer
            output_size (int): size of output layer. if zero, it is not used
            hidden_sizes (list): sizes of hidden layers
            output_activation (function): activation function of output layer
            hidden_activation (function): activation function of hidden layers
            use_output_layer (bool): whether or not to use the last layer 
                                     for using subclass
        """

        super().__init__()

        self.fcs = nn.Sequential()

        # Hidden Layers
        # ========================================================================
        prev_size = input_size
        for i, next_size in enumerate(hidden_sizes):
            self.fcs.add_module(f"hidden_fc{i}", nn.Linear(prev_size, next_size))
            self.fcs.add_module(f"hidden_fc_act{i}", hidden_activation)
            prev_size = next_size

        # Output Layers
        # ========================================================================
        if output_size:
            self.fcs.add_module(f"output", nn.Linear(prev_size, output_size))
            self.fcs.add_module(f"output_act", output_activation)
        
        self.apply(init_linear_weights_xavier)

    def forward(self, x: torch.Tensor):
        """Forward method implementation"""
        return self.fcs(x)

class NormalDistMLP(nn.Module):
    """ Multi-layer Perceptron with Normal distribution output
        It is for continuous environment
        It can be seperated or shared network
        But hidden layer size of mu, std is always same.
    """

    def __init__(
        self,
        input_size: int,
        hidden_sizes: list,
        output_size: int,
        mu_activation: Callable = nn.Sequential(), # identity
        sigma_activation: Callable = nn.Sequential(), # identity
        hidden_activation: Callable = nn.ReLU(),
        share_net: bool = True,
        std_ones: bool = False,
    ):
        '''Initialization with xavier

        Args:
            input_size (int): size of input layer
            output_size (int): size of output layer
            hidden_sizes (list): sizes of hidden layers
            mu_activation (function): activation function of mean(mu)
            sigma_activation (function): activation function of std or logstd(sigma)
            hidden_activation (function): activation function of hidden layers
            share_net (bool): whether using one network or sperate network
        '''

        super().__init__()

        self.share_net = share_net
        self.std_ones = std_ones

        if self.std_ones:
            self.mu = MLP(
                input_size=input_size,
                output_size=output_size,
                hidden_sizes=hidden_sizes,
                output_activation=mu_activation,
                hidden_activation=hidden_activation
            )

        elif share_net:
            self.hidden_layer = MLP(
                input_size=input_size,
                hidden_sizes=hidden_sizes,
                hidden_activation=hidden_activation,
            )
            
            prev_layer = hidden_sizes[-1] if hidden_sizes else input_size
            
            self.mu = nn.Sequential(
                                nn.Linear(prev_layer, output_size),
                                mu_activation
                            )

            self.sigma = nn.Sequential(
                                nn.Linear(prev_layer, output_size),
                                sigma_activation
                            )
        else:
            self.mu = MLP(
                input_size=input_size,
                output_size=output_size,
                hidden_sizes=hidden_sizes,
                output_activation=mu_activation,
                hidden_activation=hidden_activation
            )
            self.sigma = MLP(
                input_size=input_size,
                output_size=output_size,
                hidden_sizes=hidden_sizes,
                output_activation=sigma_activation,
                hidden_activation=hidden_activation
            )

        self.apply(init_linear_weights_xavier)
    
    def forward(self, x):
        if self.std_ones:
            mu = self.mu(x)
            sigma = torch.ones_like(mu)

        elif self.share_net:
            hidden_layer = self.hidden_layer.forward(x)
            
            mu = self.mu(hidden_layer)
            sigma = self.sigma(hidden_layer)
        else:
            mu = self.mu(x)
            sigma = self.sigma(x)
        
        return Normal(mu, sigma)

class GaussianDistMLP(nn.Module):

    def __init__(
        self,
        input_size: int,
        hidden_sizes: list,
        output_size: int,
        mu_activation: Callable = nn.Sequential(), # identity
        sigma_activation: Callable = nn.Tanh(), # identity
        hidden_activation: Callable = nn.ReLU(),
        share_net: bool = True,
        log_std_min: float = -20,
        log_std_max: float = 2,
    ):
        super().__init__()

        self.share_net = share_net
        self.log_std_min = log_std_min
        self.log_std_max = log_std_max

        if share_net:
            self.hidden_layer = MLP(
                input_size=input_size,
                hidden_sizes=hidden_sizes,
                hidden_activation=hidden_activation,
            )
            
            prev_layer = hidden_sizes[-1] if hidden_sizes else input_size
            
            self.mu = nn.Sequential(
                                nn.Linear(prev_layer, output_size),
                                mu_activation
                            )

            self.sigma = nn.Sequential(
                                nn.Linear(prev_layer, output_size),
                                sigma_activation
                            )
        else:
            self.mu = MLP(
                input_size=input_size,
                output_size=output_size,
                hidden_sizes=hidden_sizes,
                output_activation=mu_activation,
                hidden_activation=hidden_activation
            )
            self.sigma = MLP(
                input_size=input_size,
                output_size=output_size,
                hidden_sizes=hidden_sizes,
                output_activation=sigma_activation,
                hidden_activation=hidden_activation
            )

        self.apply(init_linear_weights_xavier)

    def forward(self, x: torch.Tensor) -> Tuple[torch.Tensor, ...]:
        if self.share_net:
            hidden_layer = self.hidden_layer.forward(x)
            
            mu = self.mu(hidden_layer)
            sigma = self.sigma(hidden_layer)
        else:
            mu = self.mu(x)
            sigma = self.sigma(x)

        # get std
        log_std = self.log_std_min + 0.5 * (self.log_std_max - self.log_std_min) * (
            sigma + 1
        )
        std = torch.exp(log_std)

        return Normal(mu, std)

class CategoricalMLP(nn.Module):
    """ Multi-layer Perceptron with categorical distribution output 
        It is for discrete environment
        It can be seperated or shared network
        But hidden layer size of mu, std is always same.
    """

    def __init__(
        self,
        input_size: int,
        output_size: int,
        hidden_sizes: list,        
        output_activation: Callable = nn.Softmax(), # identity
        hidden_activation: Callable = nn.ReLU(),
    ):
        '''Initialization with xavier

        Args:
            input_size (int): size of input layer
            output_size (int): size of output layer
            hidden_sizes (list): sizes of hidden layers
            output_activation (function): activation function of output layer
            hidden_activation (function): activation function of hidden layers
        '''

        super().__init__()

        self.fc = MLP(
            input_size=input_size,
            output_size=output_size,
            hidden_sizes=hidden_sizes,
            output_activation=output_activation,
            hidden_activation=hidden_activation
        )

        self.apply(init_linear_weights_xavier)
    
    def forward(self, x):
        output = self.fc(x)
        
        return Categorical(output)