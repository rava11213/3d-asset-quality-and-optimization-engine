#pragma once
#include "assetq/mesh.hpp"
#include <string>

namespace assetq {
struct SimplifyOptions {
    bool feature_aware = false;
    double feature_angle_degrees = 45;
    double feature_weight = 10;
    bool curvature_weighted = false;
    double curvature_strength = 4;
    bool boundary_chain = false;
    bool profile = false;
    bool sorted_refresh = true;
};
struct OptimizerProfile {
    double mesh_setup_seconds=0, curvature_seconds=0, boundary_seconds=0, feature_seconds=0;
    double initial_queue_seconds=0, queue_pop_seconds=0, validation_seconds=0;
    double mesh_update_seconds=0, queue_refresh_seconds=0, compaction_seconds=0;
    std::size_t queue_pushes=0, queue_pops=0, stale_pops=0, max_queue_entries=0;
};
struct SimplifyResult {
    Mesh mesh;
    std::size_t collapses = 0;
    std::size_t rejected_candidates = 0;
    bool target_reached = false;
    double elapsed_seconds = 0;
    std::size_t original_crease_edges = 0;
    std::size_t protected_corners = 0;
    double curvature_reference = 0;
    double minimum_face_weight = 1;
    double maximum_face_weight = 1;
    double mean_face_weight = 1;
    std::size_t original_boundary_edges = 0;
    std::size_t boundary_corners = 0;
    std::size_t boundary_collapses = 0;
    OptimizerProfile profile;
};
// Geometry only. Requires consistently wound manifold triangles. Boundary vertices
// remain fixed by default; optional boundary-chain reductions protect corners.
// Work in unit-diagonal coordinates; return original coordinates.
SimplifyResult simplify(const Mesh& mesh, std::size_t target, const SimplifyOptions& options = {});
}
