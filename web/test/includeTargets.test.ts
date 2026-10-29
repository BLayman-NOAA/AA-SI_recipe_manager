import { describe, expect, it } from 'vitest';
import { includeTargets, type Graph } from '../src/api/graph';
import { intentFor, previewIntent, selectionTargets } from '../src/runs/intent';
import hb1603Json from './fixtures/hb1603.graph.json';
import uc1Json from './fixtures/uc1.graph.json';
import { nestedInputGraph, withSubGraph } from './helpers';

const uc1 = uc1Json as unknown as Graph;
const hb1603 = hb1603Json as unknown as Graph;
const CHAIN = 'inc:sub/chain.yaml';
const LEAF = 'inc:sub/leaf.yaml';

const level = (name: string) => `inc:sub_recipes/${name}.yaml`;

describe('includeTargets', () => {
  it('takes the exit steps of each UC1 level', () => {
    expect(includeTargets(uc1, level('dive_plan'))).toEqual(['plan_dives']);
    // raw_urls feeds the per file chain and the calibration lookup; the mapping feeds level 2.
    expect(includeTargets(uc1, level('processing_lvl_0_user'))).toEqual(['query_ncei', 'build_cal_mapping']);
    expect(includeTargets(uc1, level('processing_lvl_1_per_file'))).toEqual(['combine_raw']);
    // crop_survey_range feeds no step, so the dead end is a target too.
    expect(includeTargets(uc1, level('processing_lvl_2_survey'))).toEqual(['ep_add_depth', 'crop_survey_range']);
    expect(includeTargets(uc1, level('processing_lvl_2_user'))).toEqual(['dive_remove_noise']);
    expect(includeTargets(uc1, level('processing_lvl_3_user'))).toEqual([
      'rechunk_dive_mvbs',
      'merge_dive_files_cell_stats',
    ]);
    expect(includeTargets(uc1, level('processing_lvl_4'))).toEqual(['embed_results', 'plot_clustering_report']);
    expect(includeTargets(uc1, level('processing_lvl_5_sv_codes'))).toEqual(['sv_code_depth_table']);
  });

  it('takes the exit steps of the HB1603 combined level 2', () => {
    expect(includeTargets(hb1603, level('processing_lvl_2_combined'))).toEqual([
      'crop_survey_range',
      'dive_remove_noise',
    ]);
    expect(includeTargets(hb1603, level('processing_lvl_0_user'))).toEqual(['query_ncei', 'build_cal_mapping']);
  });

  it('adds a member that gives a declared recipe output', () => {
    const graph = structuredClone(uc1);
    const output = graph.nodes.find((node) => node.id === 'out:run_hdbscan.cluster_labels');
    if (output?.kind === 'output') output.declared = true;
    expect(includeTargets(graph, level('processing_lvl_4'))).toEqual([
      'run_hdbscan',
      'embed_results',
      'plot_clustering_report',
    ]);
  });

  it('counts the steps of nested includes as members', () => {
    expect(includeTargets(withSubGraph, CHAIN)).toEqual(['leaf_add']);
    expect(includeTargets(withSubGraph, LEAF)).toEqual(['leaf_add']);
    expect(includeTargets(nestedInputGraph(), CHAIN)).toEqual(['leaf_add']);
    expect(includeTargets(withSubGraph, 'inc:missing.yaml')).toEqual([]);
  });
});

describe('run to a selected sub-recipe', () => {
  const chain = { kind: 'include', id: CHAIN } as const;

  it('targets the exit steps for Run to selected only', () => {
    expect(selectionTargets(chain, withSubGraph)).toEqual(['leaf_add']);
    expect(selectionTargets(chain, null)).toEqual([]);
    expect(selectionTargets({ kind: 'input', id: 'in:n' }, withSubGraph)).toEqual([]);
    expect(intentFor('to', chain, withSubGraph)).toEqual({
      mode: 'to',
      targets: ['leaf_add'],
      force: false,
      forceSteps: [],
      include: CHAIN,
    });
    expect(intentFor('to', chain)).toBeNull();
    expect(intentFor('only', chain, withSubGraph)).toBeNull();
    expect(intentFor('forceSelected', chain, withSubGraph)).toBeNull();
  });

  it('previews the run through it', () => {
    expect(previewIntent(chain, withSubGraph)).toMatchObject({ mode: 'to', targets: ['leaf_add'] });
    expect(previewIntent({ kind: 'include', id: level('processing_lvl_0_user') }, uc1).targets).toEqual([
      'query_ncei',
      'build_cal_mapping',
    ]);
    expect(previewIntent(chain, null)).toMatchObject({ mode: 'all', targets: [] });
  });
});
