from benchmarks.vocabulary_traversal import run_vocabulary_traversal_benchmark


def test_progressive_benchmark_is_accurate_and_inspects_under_five_percent():
    report = run_vocabulary_traversal_benchmark(node_count=1_000, branching_factor=10)

    assert report["total_taxonomy_nodes"] == 1_000
    assert report["full_vocabulary"]["classification_success"] is True
    assert report["progressive"]["classification_success"] is True
    assert report["full_vocabulary"]["retrieval_success"] is True
    assert report["progressive"]["retrieval_success"] is True
    assert report["full_vocabulary"]["taxonomy_nodes_returned"] == 1_000
    assert report["progressive"]["percentage_taxonomy_returned"] < 5.0
    assert report["progressive"]["taxonomy_nodes_returned"] < 50
    assert report["progressive"]["backtracks"] == 1
    assert report["progressive"]["wrong_branch_descents"] >= 1
    assert report["progressive"]["tool_calls"] > report["full_vocabulary"]["tool_calls"]
    assert report["progressive"]["payload_bytes"] < report["full_vocabulary"]["payload_bytes"]


def test_benchmark_rejects_invalid_shape_parameters():
    for node_count, branching_factor in ((1, 10), (100, 1), (10, 20)):
        try:
            run_vocabulary_traversal_benchmark(
                node_count=node_count, branching_factor=branching_factor,
            )
        except ValueError:
            pass
        else:
            raise AssertionError(
                f"Expected invalid benchmark shape to fail: nodes={node_count}, branching={branching_factor}"
            )
