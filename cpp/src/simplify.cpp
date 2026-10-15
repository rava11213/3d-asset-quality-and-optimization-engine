#include "assetq/simplify.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <iterator>
#include <limits>
#include <map>
#include <queue>
#include <set>
#include <stdexcept>

namespace assetq {
namespace {
using Clock=std::chrono::steady_clock;
struct Timer {
    bool enabled; double& seconds; Clock::time_point start;
    Timer(bool active,double& target):enabled(active),seconds(target),start(active?Clock::now():Clock::time_point{}) {}
    ~Timer() { if(enabled) seconds+=std::chrono::duration<double>(Clock::now()-start).count(); }
};
using Edge = std::pair<std::size_t, std::size_t>;
Edge edge(std::size_t a, std::size_t b) { return {std::min(a,b), std::max(a,b)}; }
Vec3 sub(const Vec3& a, const Vec3& b) { return {a[0]-b[0],a[1]-b[1],a[2]-b[2]}; }
Vec3 cross(const Vec3& a, const Vec3& b) { return {a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]}; }
double dot(const Vec3& a, const Vec3& b) { return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]; }
double length(const Vec3& v) { return std::hypot(v[0],v[1],v[2]); }
struct Quadric {
    double m[4][4]{};
    Quadric operator+(const Quadric& b) const {
        Quadric q;
        for (int i=0;i<4;++i) for (int j=0;j<4;++j) q.m[i][j]=m[i][j]+b.m[i][j];
        return q;
    }
    void plane(const Vec3& n, double d) {
        const double p[4]={n[0],n[1],n[2],d};
        for (int i=0;i<4;++i) for (int j=0;j<4;++j) m[i][j]+=p[i]*p[j];
    }
    double cost(const Vec3& v) const {
        const double p[4]={v[0],v[1],v[2],1};
        double c=0;
        for (int i=0;i<4;++i) for (int j=0;j<4;++j) c+=p[i]*m[i][j]*p[j];
        return std::max(0.0,c);
    }
    bool optimum(Vec3& v) const {
        double a[3][4], scale=0;
        for(int i=0;i<3;++i) {
            for(int j=0;j<3;++j) { a[i][j]=m[i][j]; scale=std::max(scale,std::abs(a[i][j])); }
            a[i][3]=-m[i][3];
        }
        if (scale==0) return false;
        for(int k=0;k<3;++k) {
            int pivot=k;
            for(int i=k+1;i<3;++i) if(std::abs(a[i][k])>std::abs(a[pivot][k])) pivot=i;
            if(std::abs(a[pivot][k])<1e-12*scale) return false;
            for(int j=k;j<4;++j) std::swap(a[k][j],a[pivot][j]);
            for(int i=k+1;i<3;++i) {
                const double factor=a[i][k]/a[k][k];
                for(int j=k;j<4;++j) a[i][j]-=factor*a[k][j];
            }
        }
        for(int i=2;i>=0;--i) {
            double x=a[i][3];
            for(int j=i+1;j<3;++j) x-=a[i][j]*v[j];
            v[i]=x/a[i][i];
            if(!std::isfinite(v[i])) return false;
        }
        return true;
    }
};
struct Candidate {
    double cost;
    std::size_t u,v,ru,rv;
    Vec3 position;
    bool operator<(const Candidate& b) const {
        if(cost!=b.cost) return cost>b.cost;
        if(u!=b.u) return u>b.u;
        return v>b.v;
    }
};
class Engine {
    Mesh mesh;
    std::vector<Quadric> quadrics;
    std::vector<std::set<std::size_t>> incident;
    std::vector<bool> alive, locked;
    std::vector<std::size_t> revision;
    std::priority_queue<Candidate> heap;
    std::size_t remaining;
    Vec3 original_center;
    double original_diagonal;
    SimplifyOptions options;
    std::set<Edge> creases;
    std::vector<bool> feature_vertex;
    std::size_t crease_count=0, corner_count=0;
    double curvature_reference=0, minimum_weight=1, maximum_weight=1, mean_weight=1;
    std::vector<std::set<std::size_t>> boundary;
    std::size_t boundary_count=0, boundary_corner_count=0;
    OptimizerProfile profile;

    std::set<std::size_t> neighbors(std::size_t v) const {
        std::set<std::size_t> n;
        for(auto f:incident[v]) for(auto x:mesh.triangles[f]) if(x!=v) n.insert(x);
        return n;
    }
    void enqueue(std::size_t u,std::size_t v) {
        if(u>v) std::swap(u,v);
        if(locked[u] || locked[v] || incident[u].empty() || incident[v].empty()) return;
        const bool boundary_edge=boundary[u].count(v)!=0;
        if((!boundary[u].empty() || !boundary[v].empty()) && !boundary_edge) return;
        const bool feature_edge=creases.count(edge(u,v))!=0;
        // Keep smooth collapses away from the original crease network. A crease
        // can shorten only along itself; it cannot merge into a neighboring patch.
        if(options.feature_aware && (feature_vertex[u] || feature_vertex[v]) && !feature_edge) return;
        const auto q=quadrics[u]+quadrics[v];
        const auto& a=mesh.vertices[u]; const auto& b=mesh.vertices[v];
        Vec3 mid={(a[0]+b[0])/2,(a[1]+b[1])/2,(a[2]+b[2])/2};
        Vec3 best=a;
        double cost=q.cost(best);
        auto consider=[&](const Vec3& p) { const double c=q.cost(p); if(c<cost) { best=p; cost=c; } };
        consider(b); consider(mid);
        Vec3 optimum{};
        if(feature_edge || boundary_edge) {
            // Minimize the quadratic on p(t)=a+t(b-a), 0<=t<=1.
            const auto direction=sub(b,a);
            double aa=0,bb=0;
            for(int i=0;i<3;++i) {
                for(int j=0;j<3;++j) {
                    aa+=direction[i]*q.m[i][j]*direction[j];
                    bb+=direction[i]*q.m[i][j]*a[j];
                }
                bb+=direction[i]*q.m[i][3];
            }
            if(aa>1e-15) {
                const double t=std::clamp(-bb/aa,0.0,1.0);
                for(int k=0;k<3;++k) optimum[k]=a[k]+t*direction[k];
                consider(optimum);
            }
        } else if(q.optimum(optimum)) consider(optimum);
        heap.push({cost,u,v,revision[u],revision[v],best});
        if(options.profile) { ++profile.queue_pushes; profile.max_queue_entries=std::max(profile.max_queue_entries,heap.size()); }
    }
    bool valid(const Candidate& c,std::set<std::size_t>& affected,std::size_t& removed) const {
        const auto nu=neighbors(c.u), nv=neighbors(c.v);
        std::set<std::size_t> common, opposite;
        std::set_intersection(nu.begin(),nu.end(),nv.begin(),nv.end(),std::inserter(common,common.end()));
        affected=incident[c.u]; affected.insert(incident[c.v].begin(),incident[c.v].end());
        removed=0;
        for(auto f:incident[c.u]) {
            const auto& t=mesh.triangles[f];
            if(std::find(t.begin(),t.end(),c.v)!=t.end()) {
                ++removed;
                for(auto x:t) if(x!=c.u && x!=c.v) opposite.insert(x);
            }
        }
        const bool boundary_edge=boundary[c.u].count(c.v)!=0;
        const std::size_t expected=boundary_edge?1:2;
        if(removed!=expected || common!=opposite || opposite.size()!=expected) return false;
        if(boundary_edge) {
            if(boundary[c.u].size()!=2 || boundary[c.v].size()!=2) return false;
            auto a=boundary[c.u], b=boundary[c.v]; a.erase(c.v); b.erase(c.u);
            if(*a.begin()==*b.begin()) return false; // Never collapse a three-edge loop.
        }
        std::set<Triangle> unique;
        for(auto f:affected) {
            const auto& t=mesh.triangles[f];
            Triangle next=t;
            for(auto& x:next) if(x==c.v) x=c.u;
            if(next[0]==next[1] || next[1]==next[2] || next[0]==next[2]) continue;
            auto sorted=next; std::sort(sorted.begin(),sorted.end());
            if(!unique.insert(sorted).second) return false;
            auto p=[&](std::size_t x) { return x==c.u ? c.position : mesh.vertices[x]; };
            const auto before=cross(sub(mesh.vertices[t[1]],mesh.vertices[t[0]]),sub(mesh.vertices[t[2]],mesh.vertices[t[0]]));
            const auto after=cross(sub(p(next[1]),p(next[0])),sub(p(next[2]),p(next[0])));
            const double a=length(before), b=length(after);
            // Bound each local normal change to acos(0.2), and reject near-zero faces.
            if(b<=1e-12 || dot(before,after)<=0.2*a*b) return false;
        }
        if(boundary_edge) {
            // Check complete post-collapse face fans around every touched vertex.
            // An edge link check alone is insufficient around open components.
            std::set<std::size_t> touched;
            for(auto f:affected) for(auto x:mesh.triangles[f]) touched.insert(x==c.v?c.u:x);
            for(auto x:touched) {
                auto fs=incident[x];
                if(x==c.u) fs.insert(incident[c.v].begin(),incident[c.v].end());
                std::map<std::size_t,std::set<std::size_t>> link;
                for(auto f:fs) {
                    auto t=mesh.triangles[f];
                    for(auto& y:t) if(y==c.v) y=c.u;
                    if(t[0]==t[1] || t[1]==t[2] || t[0]==t[2]) continue;
                    std::vector<std::size_t> other;
                    for(auto y:t) if(y!=x) other.push_back(y);
                    if(other.size()!=2) return false;
                    link[other[0]].insert(other[1]); link[other[1]].insert(other[0]);
                }
                if(link.empty()) return false;
                int endpoints=0;
                for(const auto& e:link) {
                    if(e.second.size()==1) ++endpoints;
                    else if(e.second.size()!=2) return false;
                }
                const bool on_boundary=!boundary[x].empty();
                if(endpoints!=(on_boundary?2:0)) return false;
                std::set<std::size_t> visited;
                std::vector<std::size_t> stack{link.begin()->first};
                while(!stack.empty()) {
                    auto y=stack.back(); stack.pop_back();
                    if(!visited.insert(y).second) continue;
                    for(auto z:link[y]) stack.push_back(z);
                }
                if(visited.size()!=link.size()) return false;
            }
        }
        return true;
    }
public:
    explicit Engine(const Mesh& input,const SimplifyOptions& settings):mesh(input),quadrics(input.vertices.size()),incident(input.vertices.size()),
        alive(input.triangles.size(),true),locked(input.vertices.size(),false),revision(input.vertices.size(),0),
        remaining(input.triangles.size()),original_center(center(input)),original_diagonal(diagonal(input)),
        options(settings),feature_vertex(input.vertices.size(),false),boundary(input.vertices.size()) {
        auto phase_start=options.profile?Clock::now():Clock::time_point{};
        auto mark=[&](double& seconds) { if(options.profile) { auto now=Clock::now(); seconds+=std::chrono::duration<double>(now-phase_start).count(); phase_start=now; } };
        normalize(mesh);
        std::map<Edge,std::pair<int,int>> edges;
        std::set<Triangle> unique;
        for(std::size_t f=0;f<mesh.triangles.size();++f) {
            const auto& t=mesh.triangles[f];
            auto sorted=t; std::sort(sorted.begin(),sorted.end());
            const auto normal=cross(sub(mesh.vertices[t[1]],mesh.vertices[t[0]]),sub(mesh.vertices[t[2]],mesh.vertices[t[0]]));
            const double area=length(normal);
            if(area<=1e-12 || !unique.insert(sorted).second) throw std::runtime_error("Simplification requires non-degenerate, non-duplicate triangles");
            const Vec3 n={normal[0]/area,normal[1]/area,normal[2]/area};
            for(auto v:t) { incident[v].insert(f); quadrics[v].plane(n,-dot(n,mesh.vertices[t[0]])); }
            for(int k=0;k<3;++k) {
                const auto u=t[k],v=t[(k+1)%3];
                auto& e=edges[edge(u,v)]; ++e.first; e.second+=u<v ? 1 : -1;
            }
        }
        std::vector<int> boundary_degree(mesh.vertices.size(),0);
        for(const auto& item:edges) {
            const auto [count,balance]=item.second;
            if(count>2 || (count==2 && balance!=0)) throw std::runtime_error("Simplification requires manifold edges and consistent winding");
            if(count==1) {
                const auto [u,v]=item.first;
                boundary[u].insert(v); boundary[v].insert(u); ++boundary_count;
                for(auto x:{u,v}) { locked[x]=!options.boundary_chain; ++boundary_degree[x]; }
            }
        }
        // An edge-manifold mesh can still have disconnected face fans at a vertex.
        for(std::size_t v=0;v<incident.size();++v) {
            if(incident[v].empty()) continue;
            if(boundary_degree[v]!=0 && boundary_degree[v]!=2) throw std::runtime_error("Non-manifold boundary vertex");
            std::map<std::size_t,std::set<std::size_t>> link;
            for(auto f:incident[v]) {
                std::vector<std::size_t> other;
                for(auto x:mesh.triangles[f]) if(x!=v) other.push_back(x);
                link[other[0]].insert(other[1]); link[other[1]].insert(other[0]);
            }
            std::set<std::size_t> visited;
            std::vector<std::size_t> stack{link.begin()->first};
            while(!stack.empty()) {
                auto x=stack.back(); stack.pop_back();
                if(!visited.insert(x).second) continue;
                for(auto y:link[x]) stack.push_back(y);
            }
            if(visited.size()!=link.size()) throw std::runtime_error("Non-manifold vertex with disconnected face fans");
        }
        if(options.boundary_chain) {
            const double cosine=std::cos(std::acos(-1.0)/6);
            for(std::size_t v=0;v<boundary.size();++v) if(!boundary[v].empty()) {
                const auto& n=boundary[v];
                const auto a=sub(mesh.vertices[*n.begin()],mesh.vertices[v]);
                const auto b=sub(mesh.vertices[*n.rbegin()],mesh.vertices[v]);
                if(n.size()!=2 || dot(a,b)/(length(a)*length(b))>-cosine) {
                    locked[v]=true; ++boundary_corner_count;
                }
            }
        }
        mark(profile.mesh_setup_seconds);
        if(options.curvature_weighted && options.curvature_strength>0) {
            // Static original-surface normal variation, not a claim of exact
            // differential curvature. Each interior edge contributes to its two faces.
            std::vector<Vec3> normals(mesh.triangles.size());
            std::vector<double> variation(mesh.triangles.size(),0);
            std::vector<int> degree(mesh.triangles.size(),0);
            for(std::size_t f=0;f<mesh.triangles.size();++f) {
                const auto& t=mesh.triangles[f];
                auto n=cross(sub(mesh.vertices[t[1]],mesh.vertices[t[0]]),sub(mesh.vertices[t[2]],mesh.vertices[t[0]]));
                const double norm=length(n);
                for(auto& x:n) x/=norm;
                normals[f]=n;
            }
            for(const auto& item:edges) if(item.second.first==2) {
                std::vector<std::size_t> adjacent;
                const auto [u,v]=item.first;
                std::set_intersection(incident[u].begin(),incident[u].end(),incident[v].begin(),incident[v].end(),std::back_inserter(adjacent));
                const double change=std::clamp(1-dot(normals[adjacent[0]],normals[adjacent[1]]),0.0,2.0);
                for(auto f:adjacent) { variation[f]+=change; ++degree[f]; }
            }
            std::vector<double> positive;
            for(std::size_t f=0;f<variation.size();++f) {
                if(degree[f]) variation[f]/=degree[f];
                if(variation[f]>1e-15) positive.push_back(variation[f]);
                else variation[f]=0;
            }
            if(!positive.empty()) {
                std::sort(positive.begin(),positive.end());
                const double index=.95*(positive.size()-1);
                const auto low=static_cast<std::size_t>(index);
                curvature_reference=positive[low]+(index-low)*(positive[std::min(low+1,positive.size()-1)]-positive[low]);
                std::fill(quadrics.begin(),quadrics.end(),Quadric{});
                minimum_weight=std::numeric_limits<double>::infinity();
                maximum_weight=1; mean_weight=0;
                for(std::size_t f=0;f<mesh.triangles.size();++f) {
                    const double weight=1+options.curvature_strength*std::min(1.0,variation[f]/curvature_reference);
                    minimum_weight=std::min(minimum_weight,weight);
                    maximum_weight=std::max(maximum_weight,weight);
                    mean_weight+=weight;
                    auto n=normals[f];
                    for(auto& x:n) x*=std::sqrt(weight);
                    const auto& t=mesh.triangles[f];
                    for(auto v:t) quadrics[v].plane(n,-dot(n,mesh.vertices[t[0]]));
                }
                mean_weight/=mesh.triangles.size();
            }
        }
        mark(profile.curvature_seconds);
        if(options.boundary_chain) {
            // Original boundary planes penalize movement away from open edges.
            for(const auto& item:edges) if(item.second.first==1) {
                const auto [u,v]=item.first;
                for(auto f:incident[u]) {
                    const auto& t=mesh.triangles[f];
                    if(std::find(t.begin(),t.end(),v)==t.end()) continue;
                    auto n=cross(sub(mesh.vertices[t[1]],mesh.vertices[t[0]]),sub(mesh.vertices[t[2]],mesh.vertices[t[0]]));
                    n=cross(sub(mesh.vertices[v],mesh.vertices[u]),n);
                    const double scale=std::sqrt(10.0)/length(n);
                    for(auto& x:n) x*=scale;
                    for(auto x:{u,v}) quadrics[x].plane(n,-dot(n,mesh.vertices[u]));
                }
            }
        }
        mark(profile.boundary_seconds);
        if(options.feature_aware) {
            const double radians=std::acos(-1.0)/180;
            const double threshold=std::cos(options.feature_angle_degrees*radians);
            std::vector<std::set<std::size_t>> feature_neighbors(mesh.vertices.size());
            for(const auto& item:edges) if(item.second.first==2) {
                const auto [u,v]=item.first;
                std::vector<Vec3> normals;
                for(auto f:incident[u]) {
                    const auto& t=mesh.triangles[f];
                    if(std::find(t.begin(),t.end(),v)==t.end()) continue;
                    auto n=cross(sub(mesh.vertices[t[1]],mesh.vertices[t[0]]),sub(mesh.vertices[t[2]],mesh.vertices[t[0]]));
                    const auto norm=length(n);
                    for(auto& x:n) x/=norm;
                    normals.push_back(n);
                }
                if(dot(normals[0],normals[1])>threshold) continue;
                creases.insert(item.first);
                feature_neighbors[u].insert(v); feature_neighbors[v].insert(u);
                feature_vertex[u]=feature_vertex[v]=true;
                auto tangent=sub(mesh.vertices[v],mesh.vertices[u]);
                const double norm=length(tangent);
                for(auto& x:tangent) x/=norm;
                // Each plane contains the crease and is perpendicular to an
                // adjacent face. Their quadrics penalize motion away from it.
                for(const auto& face_normal:normals) {
                    auto n=cross(tangent,face_normal);
                    const double scale=std::sqrt(options.feature_weight)/length(n);
                    for(auto& x:n) x*=scale;
                    const double d=-dot(n,mesh.vertices[u]);
                    quadrics[u].plane(n,d); quadrics[v].plane(n,d);
                }
            }
            crease_count=creases.size();
            for(std::size_t v=0;v<feature_neighbors.size();++v) {
                const auto& n=feature_neighbors[v];
                if(n.empty()) continue;
                bool corner=n.size()!=2;
                if(!corner) {
                    const auto a=sub(mesh.vertices[*n.begin()],mesh.vertices[v]);
                    const auto b=sub(mesh.vertices[*n.rbegin()],mesh.vertices[v]);
                    // Protect turns over 30 degrees from a straight chain.
                    corner=dot(a,b)/(length(a)*length(b))>-std::cos(30*radians);
                }
                if(corner) { locked[v]=true; ++corner_count; }
            }
        }
        mark(profile.feature_seconds);
        for(const auto& item:edges) enqueue(item.first.first,item.first.second);
        mark(profile.initial_queue_seconds);
    }
    SimplifyResult run(std::size_t target) {
        SimplifyResult result;
        result.original_crease_edges=crease_count;
        result.protected_corners=corner_count;
        result.curvature_reference=curvature_reference;
        result.minimum_face_weight=minimum_weight;
        result.maximum_face_weight=maximum_weight;
        result.mean_face_weight=mean_weight;
        result.original_boundary_edges=boundary_count;
        result.boundary_corners=boundary_corner_count;
        while(remaining>target && !heap.empty()) {
            Candidate c;
            {
                Timer timer(options.profile,profile.queue_pop_seconds);
                c=heap.top(); heap.pop();
                if(options.profile) ++profile.queue_pops;
                if(c.ru!=revision[c.u] || c.rv!=revision[c.v]) {
                    if(options.profile) ++profile.stale_pops;
                    continue;
                }
            }
            std::set<std::size_t> affected;
            std::size_t removed=0;
            {
                Timer timer(options.profile,profile.validation_seconds);
                if(!valid(c,affected,removed) || remaining-removed<target) { ++result.rejected_candidates; continue; }
            }
            std::set<std::size_t> changed;
            {
            Timer timer(options.profile,profile.mesh_update_seconds);
            changed=neighbors(c.u); const auto nv=neighbors(c.v);
            changed.insert(nv.begin(),nv.end()); changed.insert(c.u); changed.insert(c.v);
            for(auto f:affected) {
                auto& t=mesh.triangles[f];
                for(auto x:t) incident[x].erase(f);
                for(auto& x:t) if(x==c.v) x=c.u;
                if(t[0]==t[1] || t[1]==t[2] || t[0]==t[2]) { alive[f]=false; --remaining; }
                else for(auto x:t) incident[x].insert(f);
            }
            mesh.vertices[c.u]=c.position;
            quadrics[c.u]=quadrics[c.u]+quadrics[c.v];
            if(boundary[c.u].count(c.v)) {
                auto neighbors=boundary[c.v];
                boundary[c.u].erase(c.v);
                for(auto x:neighbors) {
                    boundary[x].erase(c.v);
                    if(x!=c.u) { boundary[x].insert(c.u); boundary[c.u].insert(x); }
                }
                boundary[c.v].clear();
                ++result.boundary_collapses;
            }
            if(feature_vertex[c.v]) {
                // Update only the crease edges at the removed endpoint.
                std::vector<Edge> replace;
                for(auto x:nv) if(creases.count(edge(c.v,x))) replace.push_back(edge(c.v,x));
                for(const auto& e:replace) {
                    creases.erase(e);
                    const auto other=e.first==c.v ? e.second : e.first;
                    if(other!=c.u) creases.insert(edge(c.u,other));
                }
                feature_vertex[c.u]=true;
                feature_vertex[c.v]=false;
            }
            ++result.collapses;
            }
            Timer refresh_timer(options.profile,profile.queue_refresh_seconds);
            for(auto x:changed) ++revision[x];
            if(options.sorted_refresh) {
                std::vector<Edge> refresh;
                refresh.reserve(changed.size()*8);
                for(auto x:changed) for(auto y:neighbors(x)) refresh.push_back(edge(x,y));
                std::sort(refresh.begin(),refresh.end());
                refresh.erase(std::unique(refresh.begin(),refresh.end()),refresh.end());
                for(const auto& e:refresh) enqueue(e.first,e.second);
            } else {
                std::set<Edge> refresh;
                for(auto x:changed) for(auto y:neighbors(x)) refresh.insert(edge(x,y));
                for(const auto& e:refresh) enqueue(e.first,e.second);
            }
        }
        {
        Timer timer(options.profile,profile.compaction_seconds);
        std::vector<std::size_t> remap(mesh.vertices.size(),std::numeric_limits<std::size_t>::max());
        for(std::size_t f=0;f<alive.size();++f) if(alive[f]) {
            auto t=mesh.triangles[f];
            for(auto& v:t) {
                if(remap[v]==std::numeric_limits<std::size_t>::max()) {
                    remap[v]=result.mesh.vertices.size();
                    auto p=mesh.vertices[v];
                    for(int k=0;k<3;++k) p[k]=p[k]*original_diagonal+original_center[k];
                    result.mesh.vertices.push_back(p);
                }
                v=remap[v];
            }
            result.mesh.triangles.push_back(t);
        }
        }
        result.profile=profile;
        result.target_reached=remaining==target;
        return result;
    }
};
}
SimplifyResult simplify(const Mesh& mesh,std::size_t target,const SimplifyOptions& options) {
    if(target<1 || target>mesh.triangles.size()) throw std::runtime_error("Triangle target must be between 1 and the input triangle count");
    const auto start=std::chrono::steady_clock::now();
    if(!std::isfinite(options.feature_angle_degrees) || options.feature_angle_degrees<=0 || options.feature_angle_degrees>=180 ||
       !std::isfinite(options.feature_weight) || options.feature_weight<0 || options.feature_weight>1e6)
        throw std::runtime_error("Feature angle must be in (0,180) and weight in [0,1000000]");
    if(!std::isfinite(options.curvature_strength) || options.curvature_strength<0 || options.curvature_strength>1000)
        throw std::runtime_error("Curvature strength must be finite and in [0,1000]");
    Engine engine(mesh,options);
    auto result=engine.run(target);
    result.elapsed_seconds=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
    return result;
}
}
