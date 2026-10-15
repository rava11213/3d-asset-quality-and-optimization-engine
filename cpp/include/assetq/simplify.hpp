#pragma once
#include "assetq/mesh.hpp"
#include <string>

namespace assetq {
struct SimplifyResult {
    Mesh mesh;
    std::size_t collapses = 0;
    std::size_t rejected_candidates = 0;
    bool target_reached = false;
    double elapsed_seconds = 0;
};
// Geometry only. Requires consistently wound manifold triangles. Boundary vertices
// remain fixed. Work in unit-diagonal coordinates; return original coordinates.
SimplifyResult simplify(const Mesh& mesh, std::size_t target);
}
