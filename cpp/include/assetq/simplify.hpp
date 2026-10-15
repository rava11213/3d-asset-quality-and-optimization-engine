#pragma once
#include "assetq/mesh.hpp"
#include <string>

namespace assetq {
struct SimplifyOptions {
    bool feature_aware = false;
    double feature_angle_degrees = 45;
    double feature_weight = 10;
};
struct SimplifyResult {
    Mesh mesh;
    std::size_t collapses = 0;
    std::size_t rejected_candidates = 0;
    bool target_reached = false;
    double elapsed_seconds = 0;
    std::size_t original_crease_edges = 0;
    std::size_t protected_corners = 0;
};
// Geometry only. Requires consistently wound manifold triangles. Boundary vertices
// remain fixed. Work in unit-diagonal coordinates; return original coordinates.
SimplifyResult simplify(const Mesh& mesh, std::size_t target, const SimplifyOptions& options = {});
}
