#include "assetq/mesh.hpp"
#include <algorithm>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>

namespace assetq {
namespace {
std::string quoted(const std::string& s) {
    std::ostringstream out;
    out << '"';
    for (unsigned char c : s) {
        if (c == '"' || c == '\\') out << '\\' << c;
        else if (c < 32) out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << int(c);
        else out << c;
    }
    out << '"';
    return out.str();
}
std::pair<Vec3, Vec3> bounds(const Mesh& m) {
    Vec3 lo = m.vertices.front(), hi = lo;
    for (const auto& v : m.vertices) for (int k = 0; k < 3; ++k) {
        lo[k] = std::min(lo[k], v[k]); hi[k] = std::max(hi[k], v[k]);
    }
    return {lo, hi};
}
void vector_json(std::ostream& o, const Vec3& v) {
    o << '[' << v[0] << ',' << v[1] << ',' << v[2] << ']';
}
}

Mesh load_obj(const std::filesystem::path& path) {
    std::ifstream in(path);
    if (!in) throw std::runtime_error("Cannot open OBJ: " + path.string());
    Mesh m;
    std::string line;
    std::size_t number = 0;
    while (std::getline(in, line)) {
        ++number;
        line = line.substr(0, line.find('#'));
        std::istringstream row(line);
        std::string kind;
        if (!(row >> kind)) continue;
        try {
            if (kind == "v") {
                Vec3 v;
                if (!(row >> v[0] >> v[1] >> v[2])) throw std::runtime_error("Expected three vertex coordinates");
                std::string extra;
                if (row >> extra) throw std::runtime_error("Only XYZ vertices are supported (no weights or colors)");
                for (double x : v) if (!std::isfinite(x)) throw std::runtime_error("Non-finite vertex coordinate");
                m.vertices.push_back(v);
            } else if (kind == "f") {
                Triangle t;
                std::string token;
                for (int k = 0; k < 3; ++k) {
                    if (!(row >> token)) throw std::runtime_error("Expected a triangle face");
                    const auto vertex = token.substr(0, token.find('/'));
                    std::size_t consumed;
                    const auto index = std::stoll(vertex, &consumed);
                    if (consumed != vertex.size() || index == 0) throw std::runtime_error("Invalid vertex index");
                    const auto resolved = index > 0 ? index - 1 : static_cast<long long>(m.vertices.size()) + index;
                    if (resolved < 0 || static_cast<unsigned long long>(resolved) >= m.vertices.size())
                        throw std::runtime_error("Vertex index out of range (forward references are unsupported)");
                    t[k] = static_cast<std::size_t>(resolved);
                }
                if (row >> token) throw std::runtime_error("Non-triangle face; triangulate before importing");
                m.triangles.push_back(t);
            } else {
                ++m.ignored_records;
            }
        } catch (const std::exception& e) {
            throw std::runtime_error("OBJ line " + std::to_string(number) + ": " + e.what());
        }
    }
    if (!in.eof()) throw std::runtime_error("Failed reading OBJ");
    if (m.vertices.empty() || m.triangles.empty()) throw std::runtime_error("OBJ must contain vertices and triangle faces");
    const double d = diagonal(m);
    if (!std::isfinite(d)) throw std::runtime_error("Coordinate range too large");
    if (d > 0 && !std::isfinite(1/d)) throw std::runtime_error("Coordinate range too small for normalization metadata");
    return m;
}

Vec3 center(const Mesh& m) {
    const auto [lo, hi] = bounds(m);
    return {lo[0]/2 + hi[0]/2, lo[1]/2 + hi[1]/2, lo[2]/2 + hi[2]/2};
}
double diagonal(const Mesh& m) {
    const auto [lo, hi] = bounds(m);
    return std::hypot(hi[0]-lo[0], hi[1]-lo[1], hi[2]-lo[2]);
}
void normalize(Mesh& m) {
    const double d = diagonal(m);
    if (d == 0) throw std::runtime_error("Cannot normalize a zero-size mesh");
    const auto c = center(m);
    for (auto& v : m.vertices) for (int k = 0; k < 3; ++k) v[k] = (v[k] - c[k]) / d;
}
void write_obj(const Mesh& m, const std::filesystem::path& path) {
    std::ofstream out(path);
    if (!out) throw std::runtime_error("Cannot write OBJ: " + path.string());
    out << "# assetq geometry-only export\n" << std::setprecision(17);
    for (const auto& v : m.vertices) out << "v " << v[0] << ' ' << v[1] << ' ' << v[2] << '\n';
    for (const auto& t : m.triangles) out << "f " << t[0]+1 << ' ' << t[1]+1 << ' ' << t[2]+1 << '\n';
    out.close();
    if (!out) throw std::runtime_error("Failed writing OBJ");
}
void write_report(const Mesh& m, const std::filesystem::path& input, const std::filesystem::path& report) {
    const auto [lo, hi] = bounds(m);
    const double d = diagonal(m);
    std::size_t degenerates = 0, duplicates = 0;
    std::set<Triangle> faces;
    std::vector<bool> used(m.vertices.size(), false);
    // Each edge stores incidence count and signed orientation balance.
    std::map<std::pair<std::size_t, std::size_t>, std::pair<int, int>> edges;
    for (const auto& t : m.triangles) {
        for (auto i : t) used[i] = true;
        auto sorted = t;
        std::sort(sorted.begin(), sorted.end());
        if (!faces.insert(sorted).second) ++duplicates;
        Vec3 a{}, b{};
        for (int k = 0; k < 3; ++k) {
            a[k] = d > 0 ? (m.vertices[t[1]][k]-m.vertices[t[0]][k])/d : 0;
            b[k] = d > 0 ? (m.vertices[t[2]][k]-m.vertices[t[0]][k])/d : 0;
        }
        const double area2 = std::hypot(a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]);
        if (area2 <= 1e-12) ++degenerates;
        // Count incidences by distinct face, including malformed faces only once per edge.
        std::set<std::pair<std::size_t, std::size_t>> seen;
        for (int k = 0; k < 3; ++k) {
            auto u = t[k], v = t[(k+1)%3];
            if (u == v) continue;
            auto key = std::make_pair(std::min(u,v), std::max(u,v));
            if (seen.insert(key).second) {
                auto& e = edges[key]; ++e.first; e.second += u < v ? 1 : -1;
            }
        }
    }
    std::size_t boundary = 0, nonmanifold = 0, inconsistent = 0;
    for (const auto& item : edges) {
        const auto [count, balance] = item.second;
        if (count == 1) ++boundary;
        if (count > 2) ++nonmanifold;
        if (count == 2 && balance != 0) ++inconsistent;
    }
    std::ofstream out(report);
    if (!out) throw std::runtime_error("Cannot write report: " + report.string());
    out << std::setprecision(17)
        << "{\n  \"schema_version\": 1,\n  \"input\": " << quoted(input.string())
        << ",\n  \"file_size_bytes\": " << std::filesystem::file_size(input)
        << ",\n  \"vertex_count\": " << m.vertices.size()
        << ",\n  \"triangle_count\": " << m.triangles.size()
        << ",\n  \"bounds_min\": "; vector_json(out,lo);
    out << ",\n  \"bounds_max\": "; vector_json(out,hi);
    out << ",\n  \"bounding_box_diagonal\": " << d
        << ",\n  \"normalization\": {\"center\": "; vector_json(out,center(m));
    out << ", \"scale\": " << (d > 0 ? 1/d : 0) << ", \"available\": " << (d > 0 ? "true" : "false") << "},"
        << "\n  \"geometry\": {"
        << "\n    \"degenerate_triangles\": " << degenerates
        << ",\n    \"degenerate_relative_double_area_threshold\": 1e-12"
        << ",\n    \"duplicate_triangles\": " << duplicates
        << ",\n    \"boundary_edges\": " << boundary
        << ",\n    \"non_manifold_edges\": " << nonmanifold
        << ",\n    \"inconsistent_winding_edges\": " << inconsistent
        << ",\n    \"isolated_vertices\": " << std::count(used.begin(),used.end(),false)
        << "\n  },\n  \"ignored_obj_records\": " << m.ignored_records << "\n}\n";
    out.close();
    if (!out) throw std::runtime_error("Failed writing report");
}
}
