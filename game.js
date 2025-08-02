import { Unit, unitTemplates } from './units.js';

export class Game{
    constructor(w,h,tile,ctx){
        this.w=w;this.h=h;this.tile=tile;this.ctx=ctx;
        this.units=[];
    }
    spawnDemo(){
        // Sufi units (west)
        this.units.push(new Unit('Sufi','Mujahid',1,5,5));
        this.units.push(new Unit('Sufi','Marksman',1,6,6));
        this.units.push(new Unit('Sufi','Mystic',1,7,7));
        // A‑Life units (east)
        this.units.push(new Unit('A-Life','Swarmling',18,5,5));
        this.units.push(new Unit('A-Life','Spitter',18,6,6));
        this.units.push(new Unit('A-Life','Stalker',18,7,7));
    }
    cellOccupied(x,y){
        return this.units.some(u=>u.x===x&&u.y===y&&u.hp>0);
    }
    nearestEnemy(unit){
        let best=null,bestDist=1e9;
        for(const u of this.units){
            if(u.faction!==unit.faction && u.hp>0){
                const d=Math.abs(u.x-unit.x)+Math.abs(u.y-unit.y);
                if(d<bestDist){best=u;bestDist=d;}
            }
        }
        return best;
    }
    update(){
        // Simple turn loop
        for(const unit of this.units){
            if(unit.hp<=0) continue;
            const enemy=this.nearestEnemy(unit);
            if(!enemy) continue;
            const dist=Math.abs(enemy.x-unit.x)+Math.abs(enemy.y-unit.y);
            if(dist<=unit.range){
                enemy.hp-=unit.atk;
            }else{
                // move toward
                const dx=Math.sign(enemy.x-unit.x);
                const dy=Math.sign(enemy.y-unit.y);
                const nx=unit.x+dx, ny=unit.y+dy;
                if(!this.cellOccupied(nx,ny) && nx>=0&&nx<this.w&&ny>=0&&ny<this.h){
                    unit.x=nx;unit.y=ny;
                }
            }
        }
        // Remove dead
        this.units=this.units.filter(u=>u.hp>0);
    }
    draw(){
        const c=this.ctx, t=this.tile;
        c.clearRect(0,0,this.w*t,this.h*t);
        // grid
        c.strokeStyle='#333';
        for(let x=0;x<=this.w;x++){c.beginPath();c.moveTo(x*t,0);c.lineTo(x*t,this.h*t);c.stroke();}
        for(let y=0;y<=this.h;y++){c.beginPath();c.moveTo(0,y*t);c.lineTo(this.w*t,y*t);c.stroke();}
        // units
        for(const u of this.units){
            c.fillStyle=u.color();
            c.fillRect(u.x*t+4,u.y*t+4,t-8,t-8);
            c.fillStyle='#fff';
            c.font='10px sans-serif';
            c.fillText(u.hp, u.x*t+6, u.y*t+14);
        }
    }
}
