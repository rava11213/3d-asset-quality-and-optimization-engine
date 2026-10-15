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

    std::set<std::size_t> neighbors(std::size_t v) const {
        std::set<std::size_t> n;
        for(auto f:incident[v]) for(auto x:mesh.triangles[f]) if(x!=v) n.insert(x);
        return n;
    }
    void enqueue(std::size_t u,std::size_t v) {
        if(u>v) std::swap(u,v);
        if(locked[u] || locked[v] || incident[u].empty() || incident[v].empty()) return;
        const auto q=quadrics[u]+quadrics[v];
        const auto& a=mesh.vertices[u]; const auto& b=mesh.vertices[v];
        Vec3 mid={(a[0]+b[0])/2,(a[1]+b[1])/2,(a[2]+b[2])/2};
        Vec3 best=a;
        double cost=q.cost(best);
        auto consider=[&](const Vec3& p) { const double c=q.cost(p); if(c<cost) { best=p; cost=c; } };
        consider(b); consider(mid);
        Vec3 optimum{};
        if(q.optimum(optimum)) consider(optimum);
        heap.push({cost,u,v,revision[u],revision[v],best});
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
        // Both endpoints are interior; the manifold edge link has exactly two vertices.
        if(removed!=2 || common!=opposite || opposite.size()!=2) return false;
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
        return true;
    }
public:
    explicit Engine(const Mesh& input):mesh(input),quadrics(input.vertices.size()),incident(input.vertices.size()),
        alive(input.triangles.size(),true),locked(input.vertices.size(),false),revision(input.vertices.size(),0),
        remaining(input.triangles.size()),original_center(center(input)),original_diagonal(diagonal(input)) {
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
            if(count==1) for(auto v:{item.first.first,item.first.second}) { locked[v]=true; ++boundary_degree[v]; }
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
        for(const auto& item:edges) enqueue(item.first.first,item.first.second);
    }
    SimplifyResult run(std::size_t target) {
        SimplifyResult result;
        while(remaining>target && !heap.empty()) {
            auto c=heap.top(); heap.pop();
            if(c.ru!=revision[c.u] || c.rv!=revision[c.v]) continue;
            std::set<std::size_t> affected;
            std::size_t removed=0;
            if(!valid(c,affected,removed) || remaining-removed<target) { ++result.rejected_candidates; continue; }
            auto changed=neighbors(c.u); const auto nv=neighbors(c.v);
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
            ++result.collapses;
            for(auto x:changed) ++revision[x];
            std::set<Edge> refresh;
            for(auto x:changed) for(auto y:neighbors(x)) refresh.insert(edge(x,y));
            for(const auto& e:refresh) enqueue(e.first,e.second);
        }
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
        result.target_reached=remaining==target;
        return result;
    }
};
}
SimplifyResult simplify(const Mesh& mesh,std::size_t target) {
    if(target<1 || target>mesh.triangles.size()) throw std::runtime_error("Triangle target must be between 1 and the input triangle count");
    const auto start=std::chrono::steady_clock::now();
    Engine engine(mesh);
    auto result=engine.run(target);
    result.elapsed_seconds=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
    return result;
}
}
