#pragma once
#include <array>
#include <filesystem>
#include <vector>

namespace assetq {
using Vec3 = std::array<double, 3>;
using Triangle = std::array<std::size_t, 3>;
struct Mesh {
    std::vector<Vec3> vertices;
    std::vector<Triangle> triangles;
    std::size_t ignored_records = 0;
};
Mesh load_obj(const std::filesystem::path& path);
void write_obj(const Mesh& mesh, const std::filesystem::path& path);
Vec3 center(const Mesh& mesh);
double diagonal(const Mesh& mesh);
void normalize(Mesh& mesh);
void write_report(const Mesh& mesh, const std::filesystem::path& input,
                  const std::filesystem::path& report);
}
