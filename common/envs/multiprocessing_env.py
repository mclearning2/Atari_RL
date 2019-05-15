# This code reference
# https://github.com/openai/baselines/tree/master/baselines/common/vec_env
# But modified a little...

import gym
import numpy as np
from multiprocessing import Process, Pipe, cpu_count

def worker(remote, parent_remote, env_fn_wrapper):
    parent_remote.close()
    env = env_fn_wrapper.x()
    while True:
        cmd, data = remote.recv()
        if cmd == 'step':
            ob, reward, done, info = env.step(data)                
            if done:
                ob = env.reset()
            remote.send((ob, reward, done, info))
        elif cmd == 'seed':
            env.seed(data)
        elif cmd == 'reset':
            ob = env.reset()
            remote.send(ob)
        elif cmd == 'reset_task':
            ob = env.reset_task()
            remote.send(ob)
        elif cmd == 'render':
            env.render()
        elif cmd == 'close':
            remote.close()
            break
        elif cmd == 'random_action':
            random_action = env.action_space.sample()
            remote.send(random_action)
        elif cmd == 'get_spaces':
            remote.send((env.observation_space, env.action_space))
        else:
            raise NotImplementedError

class VecEnv(object):
    """
    An abstract asynchronous, vectorized environment.
    """

    def __init__(self, num_envs, observation_space, action_space):
        self.num_envs = num_envs
        self.observation_space = observation_space
        self.action_space = action_space

    def reset(self):
        """
        Reset all the environments and return an array of
        observations, or a tuple of observation arrays.
        If step_async is still doing work, that work will
        be cancelled and step_wait() should not be called
        until step_async() is invoked again.
        """

    def step_async(self, actions):
        """
        Tell all the environments to start taking a step
        with the given actions.
        Call step_wait() to get the results of the step.
        You should not call this if a step_async run is
        already pending.
        """

    def step_wait(self):
        """
        Wait for the step taken with step_async().
        Returns (obs, rews, dones, infos):
         - obs: an array of observations, or a tuple of
                arrays of observations.
         - rews: an array of rewards
         - dones: an array of "episode done" booleans
         - infos: a sequence of info objects
        """

    def close(self):
        """
        Clean up the environments' resources.
        """

    def step(self, actions):
        self.step_async(actions)
        return self.step_wait()
class CloudpickleWrapper(object):
    """
    Uses cloudpickle to serialize contents (otherwise multiprocessing tries to use pickle)
    """

    def __init__(self, x):
        self.x = x

    def __getstate__(self):
        import cloudpickle
        return cloudpickle.dumps(self.x)

    def __setstate__(self, ob):
        import pickle
        self.x = pickle.loads(ob)
class MultipleEnv(VecEnv):
    def __init__(self, 
        env_id, 
        n_envs,
        max_episode_steps=None,
        monitor_func=lambda x:x
    ):
        
        self.env_id = env_id
        self.n_envs = n_envs
        self.max_episode_steps = max_episode_steps

        env_fns = self._make_env_fns(monitor_func)

        self.waiting = False
        self.closed = False        
        self.remotes, self.work_remotes = zip(*[Pipe() for _ in range(n_envs)])

        self.ps = [
            Process(
                target=worker,
                args=( 
                    work_remote,
                    remote,
                    CloudpickleWrapper(env_fn))) for (
                work_remote,
                remote,
                env_fn) in zip(
                    self.work_remotes,
                    self.remotes,
                env_fns)]
        
        for p in self.ps:
            p.daemon = True  # if the main process crashes, we should not cause things to hang
            p.start()
        for remote in self.work_remotes:
            remote.close()

        self.remotes[0].send(('get_spaces', None))
        observation_space, action_space = self.remotes[0].recv()
        VecEnv.__init__(self, self.n_envs, observation_space, action_space)
        
        self._set_space(observation_space, action_space)

    def step_async(self, actions):
        for remote, action in zip(self.remotes, actions):
            remote.send(('step', action))
        self.waiting = True 

    def step_wait(self):
        results = [remote.recv() for remote in self.remotes]
        self.waiting = False
        obs, rews, dones, infos = zip(*results)
        return np.stack(obs), np.stack(rews), np.stack(dones), infos

    def seed(self, seed):
        assert isinstance(seed, int)
        seed_start = seed
        seed_end = seed + len(self.remotes)
        seeds = np.arange(seed_start, seed_end).tolist()

        for remote, seed in zip(self.remotes, seeds):
            remote.send(('seed', seed))
        self.waiting = True

    def render(self):
        for remote in self.remotes:
            remote.send(('render', None))
        self.waiting = False

    def env(self):
        def close():
            self.close()

    def reset(self):
        for remote in self.remotes:
            remote.send(('reset', None))
        return np.stack([remote.recv() for remote in self.remotes])

    def reset_task(self):
        for remote in self.remotes:
            remote.send(('reset_task', None))
        return np.stack([remote.recv() for remote in self.remotes])

    def random_action(self):
        for remote in self.remotes:
            remote.send(('random_action', None))
        return np.stack([remote.recv() for remote in self.remotes])

    def close(self):
        if self.closed:
            return
        if self.waiting:
            for remote in self.remotes:
                remote.recv()
        for remote in self.remotes:
            remote.send(('close', None))
        for p in self.ps:
            p.join()
            self.closed = True

    def _gen_env_func(self, monitor_func= lambda x: x):
        def _thunk():
            env = gym.make(self.env_id)

            if self.max_episode_steps:
                env._max_episode_steps = self.max_episode_steps
            else:
                self.max_episode_steps = env._max_episode_steps

            if monitor_func:
                env = monitor_func(env)

            return env
        return _thunk

    def _make_env_fns(self, monitor_func):
        envs = []
        for i in range(self.n_envs):
            if i == 0:
                envs.append(self._gen_env_func(monitor_func=monitor_func))
            else:
                envs.append(self._gen_env_func())
        return envs

    def _set_space(self, observation_space, action_space):
        state_size = observation_space.shape        
        if len(state_size) == 1:
            self.state_size = state_size[0]
        else:
            self.state_size = state_size
        
        self.is_discrete = isinstance(action_space, gym.spaces.Discrete)
        if self.is_discrete:
            self.low = self.high = None
            self.action_size = action_space.n
        else:
            self.low = action_space.low
            self.high = action_space.high   
            self.action_size = action_space.shape[0]

    def __len__(self):
        return self.n_envs
    
