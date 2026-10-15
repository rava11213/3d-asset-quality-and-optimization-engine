#include "assetq/mesh.hpp"
#include <iostream>
#include <stdexcept>
#include <string>

int main(int argc, char** argv) {
    try {
        if (argc == 2 && std::string(argv[1]) == "--help") {
            std::cout << "assetq-inspect INPUT.obj --report REPORT.json [--export OUTPUT.obj] [--normalize]\n";
            return 0;
        }
        if (argc < 4) throw std::runtime_error("Usage: assetq-inspect INPUT.obj --report REPORT.json [--export OUTPUT.obj] [--normalize]");
        std::filesystem::path input = argv[1], report, output;
        bool normalized = false;
        for (int i = 2; i < argc; ++i) {
            std::string arg = argv[i];
            if (arg == "--normalize") normalized = true;
            else if ((arg == "--report" || arg == "--export") && i+1 < argc) {
                (arg == "--report" ? report : output) = argv[++i];
            } else throw std::runtime_error("Unknown or incomplete argument: " + arg);
        }
        if (report.empty()) throw std::runtime_error("--report is required");
        if (normalized && output.empty()) throw std::runtime_error("--normalize requires --export");
        auto same = [](const auto& a, const auto& b) {
            return std::filesystem::weakly_canonical(a) == std::filesystem::weakly_canonical(b) ||
                (std::filesystem::exists(a) && std::filesystem::exists(b) && std::filesystem::equivalent(a,b));
        };
        if (same(input, report) || (!output.empty() &&
            (same(input, output) || same(report, output))))
            throw std::runtime_error("Input, report, and export paths must differ");
        auto mesh = assetq::load_obj(input);
        // Validate normalization before writing any artifacts.
        auto exported = mesh;
        if (normalized) assetq::normalize(exported);
        assetq::write_report(mesh, input, report);
        if (!output.empty()) assetq::write_obj(exported, output);
        std::cout << "Inspected " << mesh.vertices.size() << " vertices, " << mesh.triangles.size() << " triangles\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "assetq-inspect: " << e.what() << '\n';
        return 1;
    }
}
