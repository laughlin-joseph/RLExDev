from enum import Enum
import numpy as np
import torch
import torch.nn.functional as F
import torch.nn as nn
from torch.distributions.normal import Normal
from torch.distributions.categorical import Categorical
import RLLib.Util.Functions as util
            
class SACReplayBuffer:
    def __init__(self, obs_dim, act_dim, size, device):
        self.obs_buf = np.zeros(util.combined_shape(size, obs_dim), dtype=np.float32)
        self.act_buf = np.zeros(util.combined_shape(size, act_dim), dtype=np.float32)
        self.rew_buf = np.zeros(size, dtype=np.float32)
        self.o_next_buf = np.zeros(util.combined_shape(size, obs_dim), dtype=np.float32)
        self.done_buf = np.zeros(size, dtype=np.float32)
        
        self.ptr, self.size, self.max_size, self.device = -1, 0, size, device

    def store(self, obs, act, rew, obs_next, done):
        self.ptr = (self.ptr+1) % self.max_size
        self.size = min(self.size+1, self.max_size)
        self.obs_buf[self.ptr] = obs
        self.act_buf[self.ptr] = act
        self.rew_buf[self.ptr] = rew
        self.o_next_buf[self.ptr] = obs_next
        self.done_buf[self.ptr] = done

    def sample_batch(self, batch_size=50):
        indexes = np.random.randint(0, self.size, size=batch_size)
        batch = dict(obs=self.obs_buf[indexes],
                     act=self.act_buf[indexes],
                     rew=self.rew_buf[indexes],
                     o_next=self.o_next_buf[indexes],
                     done=self.done_buf[indexes])
        return {k: torch.as_tensor(v, dtype=torch.float32, device=self.device) for k,v in batch.items()}

class GoalUpdateStrategy(Enum):
    FINAL = 1
    FUTURE = 2
    EPISODE = 3

#HER buffer, see https://arxiv.org/pdf/1707.01495.pdf
#k is number of virtual copies per replayed step.
class HERReplayBuffer(SACReplayBuffer):
    def __init__(self, obs_dim, act_dim, goal_dim, size, device,
                 strat=GoalUpdateStrategy.FINAL, HER_obs_pr=lambda obs: None, HER_rew_func=lambda exp:0, k=4):
        super().__init__(obs_dim, act_dim, size, device)
        self.desired_goal_buf = np.zeros(util.combined_shape(size, goal_dim), dtype=np.float32)
        self.achieved_goal_buf = np.zeros(util.combined_shape(size, goal_dim), dtype=np.float32)
        self.strat = strat
        self.k = k
        self.HER_obs_pr = HER_obs_pr
        self.HER_rew_func = HER_rew_func
    
    def store(self, obs, act, rew, obs_next, done, desired_goal, achieved_goal):
        SACReplayBuffer.store(self, obs, act, rew, obs_next, done)
        # Store achieved goal and desired goal along with the transition
        self.desired_goal_buf[self.ptr] = desired_goal
        self.achieved_goal_buf[self.ptr] = achieved_goal

    def sample_batch(self, batch_size=50):
        indexes = np.random.randint(0, self.size, size=batch_size)
        desired_goal = self.desired_goal_buf[indexes]
        batch = dict(obs=np.concatenate((self.obs_buf[indexes], desired_goal), axis=1),
                     act=self.act_buf[indexes],
                     rew=self.rew_buf[indexes],
                     o_next=np.concatenate((self.o_next_buf[indexes], desired_goal), axis=1),
                     done=self.done_buf[indexes])
        return {k: torch.as_tensor(v, dtype=torch.float32, device=self.device) for k,v in batch.items()}
    
    def run_goal_update_strategy(self, batch_size):
        start, end, cur = (self.ptr-(batch_size-1)), self.ptr, 0
        process_list = []
        
        for i in range(start, end+1):
            process_list.append({'obs':self.obs_buf[i],
                                'act':self.act_buf[i],
                                'rew':self.rew_buf[i],
                                'o_next':self.o_next_buf[i],
                                'done':self.done_buf[i],
                                'des':self.desired_goal_buf[i],
                                'ach':self.achieved_goal_buf[i]})

        batch_last = batch_size - 1
        sample_end = batch_last - self.k
        final = process_list[batch_last]['ach']
        for pos, exp in enumerate(process_list):
            match self.strat:
                case GoalUpdateStrategy.FINAL:
                    self.store(exp['obs'],
                                exp['act'],
                                self.HER_rew_func(exp),
                                exp['o_next'],
                                exp['done'],
                                final,
                                exp['ach'])
                    
                case GoalUpdateStrategy.FUTURE:
                    if pos < (sample_end):
                        future_goal = exp['ach']
                        virtIndexes = np.random.randint(pos+1, high=sample_end+1, size=self.k)
                        for idx in virtIndexes:
                            self.store(process_list[idx]['obs'],
                                        process_list[idx]['act'],
                                        self.HER_rew_func(exp),
                                        process_list[idx]['o_next'],
                                        process_list[idx]['done'],
                                        future_goal,
                                        process_list[idx]['ach'])
                
                case GoalUpdateStrategy.EPISODE:
                    ep_goal = exp['ach']
                    virtIndexes = np.random.choice(range(0, pos) + range(pos+1, high=batch_last), size=self.k)
                    for idx in virtIndexes:
                        self.store(process_list[idx]['obs'],
                                    process_list[idx]['act'],
                                    self.HER_rew_func(exp),
                                    process_list[idx]['o_next'],
                                    process_list[idx]['done'],
                                    ep_goal,
                                    process_list[idx]['ach'])

class SquashedGaussianMLPActor(nn.Module):
    @property
    def mu(self):
        if self._mu is None:
            return 0
        else:
            return self._mu

    @mu.setter
    def mu(self, value):
        self._mu = value
    
    @property
    def std(self):
        if self._std is None:
            return 0
        else:
            return self._std

    @std.setter
    def std(self, value):
        self._std = value

    @property
    def entropy(self):
        if self._entropy is None:
            return 0
        else:
            return self._entropy

    @entropy.setter
    def entropy(self, value):
        self._entropy = value

    def __init__(self, obs_dim, act_dim, hidden_sizes, discrete, num_dis_actions, activation, log_max, log_min):
        super().__init__()
        self.discrete = discrete
        self.log_max = log_max
        self.log_min = log_min
        self.mu = 0
        self.std = 0
        self.entropy = 0
        self.num_dis_actions = num_dis_actions

        if discrete:
            self.net = util.mlp(list(obs_dim) + list(hidden_sizes) + list([num_dis_actions]), activation, None)
            self.soft_max = nn.Softmax(-1)
        else:
            self.net = util.mlp(list(obs_dim) + list(hidden_sizes), activation, activation)
            self.mu_layer = nn.Linear(hidden_sizes[-1], act_dim[0])
            self.log_std_layer = nn.Linear(hidden_sizes[-1], act_dim[0])            

    def forward(self, obs, deterministic=True, with_logprob=True):
        #For discrete action spaces.
        if self.discrete:
            raw_probs = self.net(obs)
            pi_probs = self.soft_max(raw_probs)
            #https://pytorch.org/docs/stable/distributions.html#categorical
            pi_distribution = Categorical(pi_probs)
            if deterministic:
                pi_action = torch.argmax(pi_probs, axis=-1)
            else:
                pi_action = pi_distribution.sample()
            #Where pi_probs is zero, replace with a very small number, ln(0) is undefined.
            eps = pi_probs == 0.0
            eps = eps.float() * 1e-8
            pi_probs = pi_probs + eps
            logprob_pi = torch.log(pi_probs)
            probs = (logprob_pi, pi_probs)
            self.entropy = pi_probs * logprob_pi
        
        #For continuous action spaces.
        else:
            net_out = self.net(obs)
            mu = self.mu_layer(net_out)
            log_std = self.log_std_layer(net_out)
            log_std = torch.clamp(log_std, self.log_min, self.log_max)
            std = torch.exp(log_std)
            
            self.mu = mu.detach()
            self.std = std.detach()
            #https://pytorch.org/docs/stable/distributions.html#normal
            pi_distribution = Normal(mu, std)
            if deterministic:
                #Average returned when testing
                pi_action = mu
            else:
                #Sample reparameterized action 
                pi_action = pi_distribution.rsample()

            if with_logprob:
                #Original equation from paper, section C eqn 21.
                #logprob_pi = ((pi_distribution.log_prob(pi_action).sum(dim=-1) - torch.log(1 - torch.tanh(pi_action).pow(2)))).sum(dim=-1)
                #Using an equation that is more numerically stable:
                #https://github.com/tensorflow/probability/commit/ef6bb176e0ebd1cf6e25c6b5cecdd2428c22963f#diff-e120f70e92e6741bca649f04fcd907b7
                logprob_pi = pi_distribution.log_prob(pi_action).sum(axis=-1)
                logprob_pi -= (2*(np.log(2) - pi_action - F.softplus(-2*pi_action))).sum(axis=-1)
                self.entropy = logprob_pi
            else:
                logprob_pi = None
            
            probs = (logprob_pi)
            pi_action = torch.tanh(pi_action)

        return pi_action, probs


class MLPQFunction(nn.Module):
    def __init__(self, obs_dim, act_dim, hidden_sizes, discrete, num_dis_actions, activation):
        super().__init__()
        self.discrete = discrete
        self.num_dis_actions = num_dis_actions
        if discrete:
            self.q = util.mlp(list(obs_dim) + list(hidden_sizes) + list([num_dis_actions]), activation)            
        else:
            #Cat obs and act dims for input layer, add hidden layers, add output Q.
            self.q = util.mlp(list(obs_dim + act_dim) + list(hidden_sizes) + list([1]), activation)

    def forward(self, input = list):
        obs = input[0]
        if self.discrete:
            q = self.q(obs)
        else:
            #Output a 
            act = input[1]
            q = self.q(torch.cat([obs, act], axis=-1))
            q = q.squeeze(-1)
        
        return q

class MLPActorCritic(nn.Module):
    def __init__(self, obs_dim, act_dim, hidden_sizes=[256,256], discrete=False, num_dis_actions=0, activation=nn.ReLU, log_max=2, log_min=-20):
        super().__init__()

        #Build actor, critic1, critic2, targ1, targ2 networksS
        self.pi = SquashedGaussianMLPActor(obs_dim, act_dim, hidden_sizes, discrete, num_dis_actions, activation, log_max, log_min)
        self.q1 = MLPQFunction(obs_dim, act_dim, hidden_sizes, discrete, num_dis_actions, activation)
        self.q2 = MLPQFunction(obs_dim, act_dim, hidden_sizes, discrete, num_dis_actions, activation)
        self.q1targ = MLPQFunction(obs_dim, act_dim, hidden_sizes, discrete, num_dis_actions, activation)
        self.q2targ = MLPQFunction(obs_dim, act_dim, hidden_sizes, discrete, num_dis_actions, activation)
        
        #Freeze target networks, these are updated with a Polyak average.
        util.freeze_thaw_parameters(self.q1targ)
        util.freeze_thaw_parameters(self.q2targ)

    def act(self, obs, deterministic=False):
        with torch.no_grad():
            a, _ = self.pi(obs, deterministic, with_logprob=False)
            a = a.cpu().numpy() if isinstance(a, torch.Tensor) else a
            return a