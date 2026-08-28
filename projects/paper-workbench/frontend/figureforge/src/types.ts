export type Tool = 'move'|'cutout'|'erase'|'text'|'cropSprite'
export type TextObject = {id:number;type:'text';x:number;y:number;text:string;fontSize:number;color:string;fontFamily:string;rotation:number;bold:boolean;thin:boolean}
export type SpriteObject = {id:number;type:'sprite';x:number;y:number;w:number;h:number;src:string;canvas?:HTMLCanvasElement}
export type Obj = TextObject|SpriteObject
export type Hole = {x:number;y:number;w:number;h:number;color:string}
export type ProjectFile = {version:number;imageName:string;baseImageSrc:string;width:number;height:number;holes:Hole[];objects:Array<TextObject|Omit<SpriteObject,'canvas'>>;nextId:number}
