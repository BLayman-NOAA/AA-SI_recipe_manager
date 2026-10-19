import { describe, expect, it } from 'vitest';
import type { Graph } from '../src/api/graph';
import { ancestry, nearestCheckpointedDownstream } from '../src/runs/runToHere';
import { withSubGraph } from './helpers';

/** with_sub (seed -> chain_inc -> leaf_add -> total) with some steps checkpointed. */
function checkpointed(...ids: string[]): Graph {
  const graph = structuredClone(withSubGraph);
  for (const node of graph.nodes) {
    if (node.kind === 'step' && ids.includes(node.id)) node.checkpoint = 'always';
  }
  return graph;
}

describe('nearestCheckpointedDownstream', () => {
  it('finds the nearest checkpointed step downstream and the steps a run to it adds', () => {
    expect(nearestCheckpointedDownstream(checkpointed('total'), 'seed')).toEqual({
      step: 'total',
      extraSteps: ['chain_inc', 'leaf_add', 'total'],
    });
    expect(nearestCheckpointedDownstream(checkpointed('leaf_add', 'total'), 'seed')).toEqual({
      step: 'leaf_add',
      extraSteps: ['chain_inc', 'leaf_add'],
    });
  });

  it('is null when nothing downstream is checkpointed', () => {
    expect(nearestCheckpointedDownstream(withSubGraph, 'seed')).toBeNull();
    expect(nearestCheckpointedDownstream(checkpointed('seed'), 'leaf_add')).toBeNull();
    expect(nearestCheckpointedDownstream(checkpointed('total'), 'total')).toBeNull();
  });

  it('collects a step and everything it depends on', () => {
    expect([...ancestry(withSubGraph, 'leaf_add')].sort()).toEqual(['chain_inc', 'leaf_add', 'seed']);
    expect([...ancestry(withSubGraph, 'seed')]).toEqual(['seed']);
  });
});
