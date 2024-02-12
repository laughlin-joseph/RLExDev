#TODO: Turn this into a main function with **kwargs
from json import load
import gymnasium as gym
from RLLib.Agents.SAC.Agent import SACAgent
import RLLib.Agents.SAC.Core as core

env_str_dense = 'FetchPickAndPlaceDense-v2'
env_str_sparse = 'FetchPickAndPlace-v2'

train_agent = True
load_agent = False
param_file_path = ''

#Create a fetch pick and place environment with dense rewards and pass it to a SAC Agent
env = gym.make(env_str_sparse, render_mode="human", max_episode_steps=50)
#env = gym.make(env_str_sparse, max_episode_steps=50)

#Configure and train SACAgent
if train_agent:
    Agent = SACAgent(env, HER_strat=core.GoalUpdateStrategy.FUTURE)
    Agent.train()
    env.close()

if load_agent:
    pass

#TODO: Gotta save the agent after training, create a new environment, and test.
#Now test the agent
for _ in range(1000):
    action = Agent.get_action(observation['observation'])
    observation, reward, terminated, truncated, info = env.step(action)

    if terminated or truncated:
        observation, info = env.reset()

env.close()