# Copyright 2024 The Flax Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Copyright 2023 The Flax Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from __future__ import annotations

import typing as tp

from flax import struct
from flax.nnx.nnx import filterlib, graph
from flax.nnx.nnx.object import Object
from flax.nnx.nnx.variables import Variable
import jax, jax.numpy as jnp

# TODO: add tests and docstrings


class MetricState(Variable):
  """Wrapper class for Metric Variables."""

  pass


class Metric(Object):

  def __init__(self):
    raise NotImplementedError('Must override `__init__()` method.')

  def reset(self):
    raise NotImplementedError('Must override `reset()` method.')

  def update(self, **kwargs) -> None:
    raise NotImplementedError('Must override `update()` method.')

  def compute(self):
    raise NotImplementedError('Must override `compute()` method.')

  def split(self, *filters: filterlib.Filter):
    return graph.split(self, *filters)


class Average(Metric):

  def __init__(self, argname: str = 'values'):
    self.argname = argname
    self.total = MetricState(jnp.array(0, dtype=jnp.float32))
    self.count = MetricState(jnp.array(0, dtype=jnp.int32))

  def reset(self):
    self.total.value = jnp.array(0, dtype=jnp.float32)
    self.count.value = jnp.array(0, dtype=jnp.int32)

  def update(self, **kwargs):
    if self.argname not in kwargs:
      raise TypeError(f"Expected keyword argument '{self.argname}'")
    values: tp.Union[int, float, jax.Array] = kwargs[self.argname]
    self.total.value += (
        values if isinstance(values, (int, float)) else values.sum()
    )
    self.count.value += 1 if isinstance(values, (int, float)) else values.size

  def compute(self):
    return self.total.value / self.count.value


@struct.dataclass
class Statistics:
  mean: jnp.float32
  standard_error_of_mean: jnp.float32
  standard_deviation: jnp.float32


class Welford(Metric):
  """Uses Welford's algorithm to compute the mean and variance of a stream of data."""

  def __init__(self, argname: str = 'values'):
    self.argname = argname
    self.count = MetricState(jnp.array(0, dtype=jnp.int32))
    self.mean = MetricState(jnp.array(0, dtype=jnp.float32))
    self.m2 = MetricState(jnp.array(0, dtype=jnp.float32))

  def reset(self):
    self.count.value = jnp.array(0, dtype=jnp.uint32)
    self.mean.value = jnp.array(0, dtype=jnp.float32)
    self.m2.value = jnp.array(0, dtype=jnp.float32)

  def update(self, **kwargs):
    if self.argname not in kwargs:
      raise TypeError(f"Expected keyword argument '{self.argname}'")
    values: tp.Union[int, float, jax.Array] = kwargs[self.argname]
    count = 1 if isinstance(values, (int, float)) else values.size
    original_count = self.count.value
    self.count.value += count
    delta = (
        values if isinstance(values, (int, float)) else values.mean()
    ) - self.mean.value
    self.mean.value += delta * count / self.count.value
    m2 = 0.0 if isinstance(values, (int, float)) else values.var() * count
    self.m2.value += (
        m2 + delta * delta * count * original_count / self.count
    )

  def compute(self):
    variance = self.m2 / self.count
    standard_deviation = variance**0.5
    sem = standard_deviation / (self.count**0.5)
    return Statistics(
        mean=self.mean,
        standard_error_of_mean=sem,
        standard_deviation=standard_deviation,
    )


class Accuracy(Average):

  def update(self, *, logits: jax.Array, labels: jax.Array, **_):  # type: ignore[override]
    if logits.ndim != labels.ndim + 1 or labels.dtype != jnp.int32:
      raise ValueError(
          f'Expected labels.dtype==jnp.int32 and logits.ndim={logits.ndim}=='
          f'labels.ndim+1={labels.ndim + 1}'
      )
    super().update(values=(logits.argmax(axis=-1) == labels))


class MultiMetric(Metric):
  """MultiMetric class to store multiple metrics and update them in a single call.

  Example usage::

    >>> import jax, jax.numpy as jnp
    >>> from flax import nnx
    ...
    >>> logits = jax.random.normal(jax.random.key(0), (5, 2))
    >>> labels = jnp.array([1, 1, 0, 1, 0])
    >>> logits2 = jax.random.normal(jax.random.key(1), (5, 2))
    >>> labels2 = jnp.array([0, 1, 1, 1, 1])
    ...
    >>> batch_loss = jnp.array([1, 2, 3, 4])
    >>> batch_loss2 = jnp.array([3, 2, 1, 0])
    ...
    >>> metrics = nnx.MultiMetric(
    ...   accuracy=nnx.metrics.Accuracy(), loss=nnx.metrics.Average()
    ... )
    >>> metrics.compute()
    {'accuracy': Array(nan, dtype=float32), 'loss': Array(nan, dtype=float32)}
    >>> metrics.update(logits=logits, labels=labels, values=batch_loss)
    >>> metrics.compute()
    {'accuracy': Array(0.6, dtype=float32), 'loss': Array(2.5, dtype=float32)}
    >>> metrics.update(logits=logits2, labels=labels2, values=batch_loss2)
    >>> metrics.compute()
    {'accuracy': Array(0.7, dtype=float32), 'loss': Array(2., dtype=float32)}
    >>> metrics.reset()
    >>> metrics.compute()
    {'accuracy': Array(nan, dtype=float32), 'loss': Array(nan, dtype=float32)}
  """

  def __init__(self, **metrics):
    # TODO: raise error if a kwarg is passed that is in ('reset', 'update', 'compute'), since these names are reserved for methods
    self._metric_names = []
    for metric_name, metric in metrics.items():
      self._metric_names.append(metric_name)
      vars(self)[metric_name] = metric

  def reset(self):
    for metric_name in self._metric_names:
      getattr(self, metric_name).reset()

  def update(self, **updates):
    # TODO: should we give the option of updating only some of the metrics and not all? e.g. if for some kwargs==None, don't do update
    # TODO: should we raise an error if a kwarg is passed into **updates that has no match with any underlying metric? e.g. user typo
    for metric_name in self._metric_names:
      getattr(self, metric_name).update(**updates)

  def compute(self):
    return {
        f'{metric_name}': getattr(self, metric_name).compute()
        for metric_name in self._metric_names
    }
