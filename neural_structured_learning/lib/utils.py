# Copyright 2019 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Utility functions for Neural Structured Learning."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import neural_structured_learning.configs as configs

import tensorflow as tf


def normalize(tensor, norm_type, epsilon=1e-6):
  """Normalizes the values in `tensor` with respect to a specified vector norm.

  This op assumes that the first axis of `tensor` is the batch dimension, and
  calculates the norm over all other axes. For example, if `tensor` is
  `tf.constant(1.0, shape=[2, 3, 4])`, its L2 norm (calculated along all the
  dimensions other than the first dimension) will be `[[sqrt(12)], [sqrt(12)]]`.
  Hence, this tensor will be normalized by dividing by
  `[[sqrt(12)], [sqrt(12)]]`.

  Note that `tf.norm` is not used here since it only allows the norm to be
  calculated over one axis, not multiple axes.

  Args:
    tensor: a tensor to be normalized. Can have any shape with the first axis
      being the batch dimension that will not be normalized across.
    norm_type: one of `nsl.configs.NormType`, the type of vector norm.
    epsilon: a lower bound value for the norm to avoid division by 0.

  Returns:
    A normalized tensor with the same shape and type as `tensor`.
  """
  if isinstance(norm_type, str):  # Allows string to be converted into NormType.
    norm_type = configs.NormType(norm_type)

  target_axes = list(range(1, len(tensor.get_shape())))
  if norm_type == configs.NormType.INFINITY:
    norm = tf.reduce_max(
        input_tensor=tf.abs(tensor), axis=target_axes, keepdims=True)
    norm = tf.maximum(norm, epsilon)
    normalized_tensor = tensor / norm
  elif norm_type == configs.NormType.L1:
    norm = tf.reduce_sum(
        input_tensor=tf.abs(tensor), axis=target_axes, keepdims=True)
    norm = tf.maximum(norm, epsilon)
    normalized_tensor = tensor / norm
  elif norm_type == configs.NormType.L2:
    normalized_tensor = tf.nn.l2_normalize(tensor, axis=target_axes)
  else:
    raise NotImplementedError('Unrecognized or unimplemented "norm_type": %s' %
                              norm_type)
  return normalized_tensor


def maximize_within_unit_norm(weights, norm_type):
  """Solves the maximization problem weights^T*x with the constraint norm(x)=1.

  This op solves a batch of maximization problems at one time. The first axis of
  `weights` is assumed to be the batch dimension, and each "row" is treated as
  an independent maximization problem.

  This op is mainly used to generate adversarial examples (e.g., FGSM proposed
  by Goodfellow et al.). Specifically, the `weights` are gradients, and `x` is
  the adversarial perturbation. The desired perturbation is the one causing the
  largest loss increase. In this op, the loss increase is approximated by the
  dot product between the gradient and the perturbation, as in the first-order
  Taylor approximation of the loss function.

  Args:
    weights: tensor representing a batch of weights to define the maximization
      objective.
    norm_type: one of `nsl.configs.NormType`, the type of vector norm.

  Returns:
    A tensor representing a batch of adversarial perturbations as the solution
    to the maximization problems. The returned tensor has the same shape and
    type as the input `weights`.
  """
  if isinstance(norm_type, str):  # Allows string to be converted into NormType.
    norm_type = configs.NormType(norm_type)

  if norm_type == configs.NormType.INFINITY:
    return tf.sign(weights)
  elif norm_type == configs.NormType.L2:
    return normalize(weights, norm_type)
  elif norm_type == configs.NormType.L1:
    # For L1 norm, the solution is to put 1 or -1 at a dimension with maximum
    # absolute value, and 0 at others. In case of multiple dimensions having the
    # same maximum absolute value, any distribution among them will do. Here we
    # choose to distribute evenly among those dimensions for efficient
    # implementation.
    target_axes = list(range(1, len(weights.get_shape())))
    abs_weights = tf.abs(weights)
    max_elem = tf.reduce_max(
        input_tensor=abs_weights, axis=target_axes, keepdims=True)
    mask = tf.compat.v1.where(
        tf.equal(abs_weights, max_elem), tf.sign(weights),
        tf.zeros_like(weights))
    num_nonzero = tf.reduce_sum(
        input_tensor=tf.abs(mask), axis=target_axes, keepdims=True)
    return mask / num_nonzero
  else:
    raise NotImplementedError('Unrecognized or unimplemented "norm_type": %s' %
                              norm_type)


def get_target_indices(logits, labels, adv_target_config):
  """Selects targeting classes for adversarial attack (classification only).

  Args:
    logits: tensor of shape `[batch_size, num_classes]` and dtype=`tf.float32`.
    labels: `int` tensor with a shape of `[batch_size]` containing the ground
      truth labels.
    adv_target_config: instance of `nsl.configs.AdvTargetConfig` specifying
      the adversarial target configuration.

  Returns:
    Tensor of shape `[batch_size]` and dtype=`tf.int32` of indices of targets.
  """
  num_classes = tf.shape(input=logits)[-1]
  if adv_target_config.target_method == configs.AdvTargetType.SECOND:
    _, top2_indices = tf.nn.top_k(logits, k=2)
    indices = tf.reshape(top2_indices[:, 1], [-1])
  elif adv_target_config.target_method == configs.AdvTargetType.LEAST:
    indices = tf.argmin(input=logits, axis=-1, output_type=tf.dtypes.int32)
  elif adv_target_config.target_method == configs.AdvTargetType.RANDOM:
    batch_size = tf.shape(input=logits)[0]
    indices = tf.random.uniform([batch_size],
                                maxval=num_classes,
                                dtype=tf.dtypes.int32,
                                seed=adv_target_config.random_seed)
  elif adv_target_config.target_method == configs.AdvTargetType.GROUND_TRUTH:
    indices = labels
  else:
    raise NotImplementedError('Unrecognized or unimplemented "target_method"')
  return indices


def _replicate_index(index_array, replicate_times):
  """Replicates index in `index_array` by the values in `replicate_times`."""
  batch_size = tf.shape(input=replicate_times)[0]
  replicated_idx_array = tf.TensorArray(
      dtype=tf.dtypes.int32, size=batch_size, infer_shape=False)
  init_iter = tf.constant(0)

  index_less_than_batch_size = lambda i, *unused_args: i < batch_size

  def duplicate_index(i, outputs):
    """Duplicates the current index by the value in the replicate_times."""
    outputs = outputs.write(i, tf.tile([index_array[i]], [replicate_times[i]]))
    return i + 1, outputs

  # Replicate the indices by the number of times indicated in 'replicate_times'.
  # For example, given `index_array = [0, 1, 2]`, `replicate_times = [3, 0, 1]`,
  # the `replicated_idx_array`  will be `[[0, 0, 0], [2]]`.
  unused_iter, replicated_idx_array = tf.while_loop(
      cond=index_less_than_batch_size,
      body=duplicate_index,
      loop_vars=[init_iter, replicated_idx_array])
  # Concats 'replicated_idx_array' as a single tensor, which can be  used for
  # duplicating the input embeddings by 'embedding_lookup'.
  replicated_idx = tf.reshape(replicated_idx_array.concat(), shape=[-1])
  return replicated_idx


def replicate_embeddings(embeddings, replicate_times):
  """Replicates the given `embeddings` by `replicate_times`.

  This function is useful when comparing the same instance with multiple other
  instances. For example, given a seed and its neighbors, this function can be
  used to replicate the embeddings of the seed by the number of its neighbors,
  such that the distances between the seed and its neighbors can be computed
  efficiently.

  The `replicate_times` argument is either a scalar, or a 1-D tensor.
  For example, if

  ```
  embeddings = [[0, 1, 2], [3, 4, 5], [6, 7, 8]]
  ```

  then we would have the following results for different `replicate_times`
  arguments:

  ```
  replicate_times = 2
  result = [[0, 1, 2], [0, 1, 2], [3, 4, 5], [3, 4, 5], [6, 7, 8], [6, 7, 8]]
  ```

  and

  ```
  replicate_times = [3, 0, 1]
  result = [[0, 1, 2], [0, 1, 2], [0, 1, 2], [6, 7, 8]]
  ```

  Args:
    embeddings: A Tensor of shape `[batch_size, d1, ..., dN]`.
    replicate_times: An integer scalar or an integer 1-D Tensor of shape `[batch
      size]`. Each element indicates the number of times the corresponding row
      in `embeddings` should be replicated.

  Returns:
    A Tensor of shape `[N, d1, ..., dN]`, where `N` is the sum of all elements
      in `replicate_times`.

  Raises:
    InvalidArgumentError: If any value in `replicate_times` is negative.
    TypeError: If `replicate_times` contains any value that cannot be cast to
      the `int32` type.
  """
  with tf.control_dependencies(
      [tf.debugging.assert_greater_equal(replicate_times, 0)]):
    replicate_times = tf.cast(replicate_times, tf.dtypes.int32)
    batch_size = tf.shape(input=embeddings)[0]
    idx_array = tf.range(batch_size, dtype='int32')
    if replicate_times.get_shape().ndims == 0:
      lookup_idx = tf.tile(tf.expand_dims(idx_array, -1), [1, replicate_times])
      lookup_idx = tf.reshape(lookup_idx, [batch_size * replicate_times])
    else:
      lookup_idx = _replicate_index(idx_array, replicate_times)
    output_embeddings = tf.gather(embeddings, lookup_idx)
    return output_embeddings


def _select_decay_fn(key):
  if key == configs.DecayType.EXPONENTIAL_DECAY:
    return tf.compat.v1.train.exponential_decay
  elif key == configs.DecayType.INVERSE_TIME_DECAY:
    return tf.compat.v1.train.inverse_time_decay
  elif key == configs.DecayType.NATURAL_EXP_DECAY:
    return tf.compat.v1.train.natural_exp_decay
  else:
    raise ValueError('Invalid configs.DecayType %s.' % key)


def decay_over_time(global_step, decay_config, init_value=1.0):
  r"""Returns a decayed value of `init_value` over time.

  When training a model with a regularizer, the objective function can be
  formulated as the following:
  $$objective = \lambda_1 * loss + \lambda_2 * regularization$$

  This function can be used for three cases:

  1. Incrementally diminishing the importance of the loss term, by applying a
     decay function to the $$\lambda_1$$ over time. We'll denote this by writing
     $$\lambda_1$$ = decay_over_time(`init_value`).
  2. Incrementally increasing the importance of the regularization term, by
     setting $$\lambda_2$$ = `init_value` - decay_over_time(`init_value`).
  3. Combining the above two cases, namely, setting $$\lambda_1$$ =
     decay_over_time(`init_value`) and $$\lambda_2$$ = `init_value` -
     decay_over_time(`init_value`).

  This function requires a `global_step` value to compute the decayed value.

  Args:
    global_step: A scalar `int32` or `int64` Tensor or a Python number. Must be
      positive.
    decay_config: A `nsl.configs.DecayConfig` for computing the decay value.
    init_value: A scalar Tensor to set the initial value to be decayed.

  Returns:
    A scalar `float` Tensor.
  """
  decayed_value = tf.cast(init_value, tf.dtypes.float32)
  decay_fn = _select_decay_fn(decay_config.decay_type)
  decayed_value = decay_fn(
      decayed_value,
      global_step=global_step,
      decay_steps=decay_config.decay_steps,
      decay_rate=decay_config.decay_rate)
  decayed_value = tf.maximum(decayed_value, decay_config.min_value)
  return decayed_value


def apply_feature_mask(features, feature_mask=None):
  """Applies a feature mask on `features` if the `feature_mask` is not `None`.

  Args:
    features: A dense tensor representing features.
    feature_mask: A dense tensor with values in `[0, 1]` and a broadcastable
      shape to `features`. If not set, or set to `None`, the `features` are
      returned unchanged.

  Returns:
    A dense tensor having the same shape as `features`.
  """
  if feature_mask is None:
    return features
  # feature_mask values need to be in [0, 1].
  with tf.control_dependencies([
      tf.debugging.assert_greater_equal(feature_mask, 0.0),
      tf.debugging.assert_less_equal(feature_mask, 1.0)
  ]):
    return features * tf.cast(feature_mask, features.dtype)


def _interleave_and_merge(tensors,
                          pre_merge_dynamic_shape_tensor,
                          keep_rank,
                          is_sparse=False):
  """Concatenates a list of tensors in an interleaved manner.

  For example, suppose `pre_merge_dynamic_shape_tensor` is `[B, D_1, D_2, ...,
  D_d]`, where `B` is the batch size. For sparse tensors (i.e., when `is_sparse`
  is `True`), the interleaving is obtained by first expanding the dimension of
  each tensor on axis 1 and then concatenating the tensors along axis 1. For
  dense tensors (i.e., when `is_sparse` is `False`), the interleaving is
  obtained by stacking tensors along axis 1. In both cases, the resulting shape
  of the interleaved tensor will be `[B, N, D_1, D_2, ...D_d]`, where `N` is the
  number of entries in `tensors`. If `keep_rank` is `True`, the original rank
  and the original sizes of all dimensions except for the first dimension are
  retained; the interleaved tensor is reshaped to `[(BxN), D_1, D_2, ...D_d]`.
  If `keep_rank` is `False`, then the interleaved tensor is returned as is.

  Args:
    tensors: List of tensors with compatible shapes. Either all of them should
      be dense or all of them should be sparse.
    pre_merge_dynamic_shape_tensor: A 1-D tensor representing the dynamic shape
      of each tensor in `tensors`.
    keep_rank: Boolean indicating whether to retain the rank from the input or
      to introduce a new dimension (axis 1).
    is_sparse: (optional) Boolean indicating if entries in `tensors` are sparse
      or not.

  Returns:
    An interleaved concatenation of `tensors`. If `keep_rank` is `True`, the
    rank is the same compared to entries in `tensors`, but the size of its first
    dimension is multiplied by a factor of the number of entries in `tensors`.
    Otherwise, the result will have rank one more than the rank of `tensors`,
    where the size of the new dimension (axis 1) is equal to the
    number of entries in `tensors`.  Note that if `tensors` is empty, then a
    value of `None` is returned.

  Raises:
    ValueError: If any entry in `tensors` has an incompatible shape.
  """
  if not tensors:
    return None
  # The first dimension in the resulting interleaved tensor will be inferred.
  merged_shape = tf.concat(
      [tf.constant([-1]), pre_merge_dynamic_shape_tensor[1:]], axis=0)

  if is_sparse:
    # This is the equivalent of tf.stack() for sparse tensors.
    concatenated_tensors = tf.sparse.concat(
        axis=1, sp_inputs=[tf.sparse.expand_dims(t, 1) for t in tensors])
    return (concatenated_tensors if keep_rank else tf.sparse.reshape(
        concatenated_tensors, shape=merged_shape))
  else:
    stacked_tensors = tf.stack(tensors, axis=1)
    return (stacked_tensors if keep_rank else tf.reshape(
        stacked_tensors, shape=merged_shape))


def unpack_neighbor_features(features, neighbor_config, keep_rank=False):
  """Extracts sample features, neighbor features, and neighbor weights.

  For example, suppose `features` contains a single sample feature named
  'F0', the batch size is 2, and each sample has 3 neighbors. Then `features`
  might look like the following:

  ```
  features = {
      'F0': tf.constant(11.0, shape=[2, 4]),
      'NL_nbr_0_F0': tf.constant(22.0, shape=[2, 4]),
      'NL_nbr_0_weight': tf.constant(0.25, shape=[2, 1]),
      'NL_nbr_1_F0': tf.constant(33.0, shape=[2, 4]),
      'NL_nbr_1_weight': tf.constant(0.75, shape=[2, 1]),
      'NL_nbr_2_F0': tf.constant(44.0, shape=[2, 4]),
      'NL_nbr_2_weight': tf.constant(1.0, shape=[2, 1]),
  },
  ```

  where `NL_nbr_<i>_F0` represents the corresponding neighbor features for the
  sample feature 'F0', and `NL_nbr_<i>_weight` represents its neighbor weights.
  The specific values for each key (tensors) in this dictionary are for
  illustrative purposes only. The first dimension of all tensors is the batch
  size.

  Example invocation:

  ```
  neighbor_config = nsl.configs.make_graph_reg_config(max_neighbors=3)
  sample_features, nbr_features, nbr_weights = nsl.lib.unpack_neighbor_features(
      features, neighbor_config)
  ```

  After performing these calls, we would have `sample_features` set to:

  ```
  { 'F0': tf.constant(11.0, shape=[2, 4]) },
  ```

  `neighbor_features` set to:

  ```
  # The key in this dictionary will contain the original sample's feature name.
  # The shape of the corresponding tensor will be 6x4, which is the result of
  # doing an interleaved merge of three 2x4 tensors along axis 0.
  {
    'F0': tf.constant([[22, 22, 22, 22], [33, 33, 33, 33], [44, 44, 44, 44],
                       [22, 22, 22, 22], [33, 33, 33, 33], [44, 44, 44, 44]]),
  },
  ```
  and `neighbor_weights` set to:

  ```
  # The shape of this tensor is 6x1, which is the result of doing an
  # interleaved merge of three 2x1 tensors along axis 0.
  tf.constant([[0.25], [0.75], [1.0], [0.25], [0.75], [1.0]])
  ```

  Args:
    features: Dictionary of tensors mapping feature names (sample features,
      neighbor features, and neighbor weights) to tensors. For each sample
      feature, all its corresponding neighbor features and neighbor weights must
      be included. All tensors should have a rank that is at least 2, where the
      first dimension is the batch size. The shape of every sample feature
      tensor should be identical to each of its corresponding neighbor feature
      tensors. The shape of each neighbor weight tensor is expected to be `[B,
      1]`, where `B` is the batch size. Neighbor weight tensors cannot be sparse
      tensors.
    neighbor_config: An instance of `nsl.configs.GraphNeighborConfig`.
    keep_rank: Whether to preserve the neighborhood size dimension. Defaults to
      `False`.

  Returns:
    sample_features: a dictionary mapping feature names to tensors. The shape
      of these tensors remains unchanged from the input.
    neighbor_features: a dictionary mapping feature names to tensors, where
      these feature names are identical to the corresponding feature names in
      `sample_features`. Further, for each feature in this dictionary, the
      resulting tensor represents an interleaved concatenated version of all
      corresponding neighbor feature tensors that exist. So, if the original
      sample feature has a shape `[B, D_1, D_2, ...., D_d]`, then the shape of
      the returned `neighbor_features` will be `[(BxN), D_1, D_2, ..., D_d]` if
      `keep_rank` is `True`, and `[B, N, D_1, D_2, ..., D_d]` if `keep_rank` is
      `False`. If `num_neighbors` is 0, then an empty dictionary is returned.
    neighbor_weights: a tensor containing floating point weights. If `keep_rank`
      is True, `neighbor_weights` will have shape `[(BxN), 1]`. Otherwise, it
      will have shape `[B, N, 1]` This also represents an interleaved
      concatenation of neighbor weight values across all neighbors. The rank of
      this tensor remains unchanged. If `num_neighbors` is 0, then a value of
      `None` is returned.

  Raises:
    KeyError: If the input does not contain all corresponding neighbor features
      for every sample feature.
    ValueError: If the tensors of samples and corresponding neighbors don't have
      the same shape.
  """

  def check_shape_compatibility(tensors, expected_shape):
    """Checks shape compatibility of the given tensors with `expected_shape`.

    Args:
      tensors: List of tensors whose static shapes will be checked for
        compatibility with `expected_shape`.
      expected_shape: Instance of `TensorShape` representing the expected static
        shape of each tensor in `tensors`.
    """
    for tensor in tensors:
      tensor.get_shape().assert_is_compatible_with(expected_shape)

  # Iterate through the 'features' dictionary to populate sample_features,
  # neighbor_features, and neighbor_weights in one pass.
  sample_features = dict()
  neighbor_features = dict()
  for feature_name, feature_value in features.items():
    # Every value in 'features' is expected to have rank > 1, i.e, 'features'
    # should have been batched to include the extra batch dimension.
    feature_shape = feature_value.get_shape().with_rank_at_least(2)

    if feature_name.startswith(neighbor_config.prefix):
      continue

    sample_features[feature_name] = feature_value

    # If graph_reg_config.max_neighbors is 0, then neighbor_feature_list will
    # be empty.
    neighbor_feature_list = [
        features['{}{}_{}'.format(neighbor_config.prefix, i, feature_name)]
        for i in range(neighbor_config.max_neighbors)
    ]

    # For a given sample feature, aggregate all of its corresponding neighbor
    # features together. Achieve this by doing an interleaved merge of the
    # neighbor feature tensors across all neighbors.

    # Populate the 'neighbor_features' dictionary only if there at least one
    # neighbor feature.
    if neighbor_feature_list:
      check_shape_compatibility(neighbor_feature_list, feature_shape)
      neighbor_features[feature_name] = _interleave_and_merge(
          neighbor_feature_list,
          tf.shape(input=feature_value),
          keep_rank,
          is_sparse=isinstance(feature_value, tf.sparse.SparseTensor))

  # If num_neighbors is 0, then neighbor_weights_list will be empty and
  # neighbor_weights will be 'None'.
  neighbor_weights_list = [
      features['{}{}{}'.format(neighbor_config.prefix, i,
                               neighbor_config.weight_suffix)]
      for i in range(neighbor_config.max_neighbors)
  ]

  # Neighbor weight tensors should have a shape of [B, 1].
  check_shape_compatibility(neighbor_weights_list, [None, 1])
  neighbor_weights = _interleave_and_merge(neighbor_weights_list, [-1, 1],
                                           keep_rank)

  return sample_features, neighbor_features, neighbor_weights
