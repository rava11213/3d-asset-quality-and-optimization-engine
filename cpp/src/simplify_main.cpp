#include "assetq/simplify.hpp"
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#ifndef __VERSION__
#define __VERSION__ "compiler version unavailable"
#endif
#if defined(__APPLE__) || defined(__unix__)
#include <sys/resource.h>
#endif

long long peak_rss_bytes() {
#if defined(__APPLE__) || defined(__unix__)
    rusage usage{};
    if(getrusage(RUSAGE_SELF,&usage)!=0) return -1;
#if defined(__APPLE__)
    return usage.ru_maxrss;
#else
    return static_cast<long long>(usage.ru_maxrss)*1024;
#endif
#else
    return -1;
#endif
}

int main(int argc,char** argv) {
    try {
        const std::string usage="assetq-simplify INPUT.obj --target TRIANGLES --export OUTPUT.obj --report RESULT.json [--method standard|feature|curvature|combined] [--feature-angle 45] [--feature-weight 10] [--curvature-strength 4] [--boundary-policy lock|chain] [--queue-refresh tree|sorted] [--profile on|off]";
        if(argc==2 && std::string(argv[1])=="--help") { std::cout<<usage<<'\n'; return 0; }
        if(argc<8) throw std::runtime_error("Usage: "+usage);
        std::filesystem::path input=argv[1],output,report;
        std::size_t target=0;
        assetq::SimplifyOptions options;
        for(int i=2;i<argc;++i) {
            const std::string arg=argv[i];
            if(i+1>=argc) throw std::runtime_error("Missing value for "+arg);
            const std::string value=argv[++i];
            if(arg=="--export") output=value;
            else if(arg=="--report") report=value;
            else if(arg=="--profile") {
                if(value!="on" && value!="off") throw std::runtime_error("Profile must be on or off");
                options.profile=value=="on";
            }
            else if(arg=="--queue-refresh") {
                if(value!="tree" && value!="sorted") throw std::runtime_error("Queue refresh must be tree or sorted");
                options.sorted_refresh=value=="sorted";
            }
            else if(arg=="--boundary-policy") {
                if(value!="lock" && value!="chain") throw std::runtime_error("Boundary policy must be lock or chain");
                options.boundary_chain=value=="chain";
            }
            else if(arg=="--method") {
                if(value!="standard" && value!="feature" && value!="curvature" && value!="combined") throw std::runtime_error("Method must be standard, feature, curvature, or combined");
                options.feature_aware=value=="feature" || value=="combined";
                options.curvature_weighted=value=="curvature" || value=="combined";
            }
            else if(arg=="--feature-angle" || arg=="--feature-weight" || arg=="--curvature-strength") {
                std::size_t used=0;
                const auto parsed=std::stod(value,&used);
                if(used!=value.size()) throw std::runtime_error("Invalid numeric option: "+arg);
                if(arg=="--feature-angle") options.feature_angle_degrees=parsed;
                else if(arg=="--feature-weight") options.feature_weight=parsed;
                else options.curvature_strength=parsed;
            }
            else if(arg=="--target") {
                if(value.empty() || value.find_first_not_of("0123456789")!=std::string::npos) throw std::runtime_error("Target must be a positive integer");
                std::size_t used=0;
                const auto parsed=std::stoull(value,&used);
                target=static_cast<std::size_t>(parsed);
                if(used!=value.size() || target!=parsed) throw std::runtime_error("Invalid target");
            } else throw std::runtime_error("Unknown argument: "+arg);
        }
        if(output.empty() || report.empty() || target==0) throw std::runtime_error("Usage: "+usage);
        auto same=[](const auto& a,const auto& b) {
            return std::filesystem::weakly_canonical(a)==std::filesystem::weakly_canonical(b) ||
                (std::filesystem::exists(a) && std::filesystem::exists(b) && std::filesystem::equivalent(a,b));
        };
        if(same(input,output) || same(input,report) || same(output,report)) throw std::runtime_error("Input, export, and report paths must differ");
        const auto mesh=assetq::load_obj(input);
        const auto result=assetq::simplify(mesh,target,options);
        assetq::write_obj(result.mesh,output);
        std::ofstream out(report);
        if(!out) throw std::runtime_error("Cannot write simplification report");
        out<<std::setprecision(17)
           <<"{\n  \"schema_version\": 1,\n  \"algorithm\": \"QEM\","
           <<"\n  \"original_triangles\": "<<mesh.triangles.size()
           <<",\n  \"method\": \""<<(options.curvature_weighted?(options.feature_aware?"combined":"curvature"):(options.feature_aware?"feature":"standard"))<<"\""
           <<",\n  \"curvature_proxy\": \""<<(options.curvature_weighted?"mean_adjacent_face_normal_disagreement":"disabled")<<"\""
           <<",\n  \"curvature_strength\": "<<options.curvature_strength
           <<",\n  \"curvature_positive_p95_reference\": "<<result.curvature_reference
           <<",\n  \"minimum_face_weight\": "<<result.minimum_face_weight
           <<",\n  \"maximum_face_weight\": "<<result.maximum_face_weight
           <<",\n  \"mean_face_weight\": "<<result.mean_face_weight
           <<",\n  \"feature_angle_degrees\": "<<options.feature_angle_degrees
           <<",\n  \"feature_weight\": "<<options.feature_weight
           <<",\n  \"original_crease_edges\": "<<result.original_crease_edges
           <<",\n  \"protected_corners\": "<<result.protected_corners
           <<",\n  \"feature_policy\": \""<<(options.feature_aware?"original_crease_network_segment_constraints":"disabled")<<"\""
           <<",\n  \"target_triangles\": "<<target
           <<",\n  \"achieved_triangles\": "<<result.mesh.triangles.size()
           <<",\n  \"original_vertices\": "<<mesh.vertices.size()
           <<",\n  \"achieved_vertices\": "<<result.mesh.vertices.size()
           <<",\n  \"triangle_reduction\": "<<1.0-double(result.mesh.triangles.size())/mesh.triangles.size()
           <<",\n  \"collapses\": "<<result.collapses
           <<",\n  \"rejected_candidates\": "<<result.rejected_candidates
           <<",\n  \"target_reached\": "<<(result.target_reached?"true":"false")
           <<",\n  \"stop_reason\": \""<<(result.target_reached?"target_reached":"no_valid_collapse_within_budget")<<"\""
           <<",\n  \"boundary_policy\": \""<<(options.boundary_chain?"constrained_boundary_chain":"lock_boundary_vertices")<<"\""
           <<",\n  \"original_boundary_edges\": "<<result.original_boundary_edges
           <<",\n  \"protected_boundary_corners\": "<<result.boundary_corners
           <<",\n  \"boundary_collapses\": "<<result.boundary_collapses
           <<",\n  \"boundary_plane_weight\": "<<(options.boundary_chain?10:0)
           <<",\n  \"boundary_corner_turn_degrees\": 30"
           <<",\n  \"minimum_normal_cosine\": 0.2"
           <<",\n  \"elapsed_seconds\": "<<result.elapsed_seconds
           <<",\n  \"compiler\": "<<std::quoted(__VERSION__)
           <<",\n  \"queue_refresh\": \""<<(options.sorted_refresh?"sorted":"tree")<<"\""
           <<",\n  \"peak_process_rss_bytes\": ";
        const auto rss=peak_rss_bytes();
        if(rss<0) out<<"null"; else out<<rss;
        out<<",\n  \"profile\": ";
        if(!options.profile) out<<"null";
        else {
            const auto& p=result.profile;
            out<<"{\"mesh_setup_seconds\":"<<p.mesh_setup_seconds
               <<",\"curvature_seconds\":"<<p.curvature_seconds
               <<",\"boundary_seconds\":"<<p.boundary_seconds
               <<",\"feature_seconds\":"<<p.feature_seconds
               <<",\"initial_queue_seconds\":"<<p.initial_queue_seconds
               <<",\"queue_pop_seconds\":"<<p.queue_pop_seconds
               <<",\"validation_seconds\":"<<p.validation_seconds
               <<",\"mesh_update_seconds\":"<<p.mesh_update_seconds
               <<",\"queue_refresh_seconds\":"<<p.queue_refresh_seconds
               <<",\"compaction_seconds\":"<<p.compaction_seconds
               <<",\"queue_pushes\":"<<p.queue_pushes<<",\"queue_pops\":"<<p.queue_pops
               <<",\"stale_pops\":"<<p.stale_pops<<",\"max_queue_entries\":"<<p.max_queue_entries<<"}";
        }
        out
           <<",\n  \"input_file_size_bytes\": "<<std::filesystem::file_size(input)
           <<",\n  \"output_file_size_bytes\": "<<std::filesystem::file_size(output)<<"\n}\n";
        out.close();
        if(!out) throw std::runtime_error("Failed writing simplification report");
        std::cout<<mesh.triangles.size()<<" -> "<<result.mesh.triangles.size()<<" triangles (target "<<target<<"): "
                 <<(result.target_reached?"target reached":"stopped: no valid collapse within budget")<<'\n';
        return 0;
    } catch(const std::exception& e) { std::cerr<<"assetq-simplify: "<<e.what()<<'\n'; return 1; }
}
