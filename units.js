export const unitTemplates={
    'Sufi':{
        'Mujahid':{hp:40,atk:6,range:1,color:'#d4af37'},
        'Marksman':{hp:30,atk:4,range:3,color:'#99cfff'},
        'Mystic':{hp:28,atk:2,range:2,color:'#ff99ff'}
    },
    'A-Life':{
        'Swarmling':{hp:22,atk:5,range:1,color:'#ff6666'},
        'Spitter':{hp:26,atk:4,range:3,color:'#66ff66'},
        'Stalker':{hp:34,atk:3,range:1,color:'#ffcc66'}
    }
};

export class Unit{
    constructor(faction,kind,x,y,idx){
        const tpl=unitTemplates[faction][kind];
        this.faction=faction;
        this.kind=kind;
        this.hp=tpl.hp;
        this.atk=tpl.atk;
        this.range=tpl.range;
        this.x=x;this.y=y;
    }
    color(){
        return unitTemplates[this.faction][this.kind].color;
    }
}
