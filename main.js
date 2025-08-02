import { Game } from './game.js';
const canvas = document.getElementById('game');
const ctx = canvas.getContext('2d');
const game = new Game(20, 12, 32, ctx); // gridW, gridH, tileSize
game.spawnDemo();       // spawn 3 Sufi + 3 A‑Life units
game.draw();

// Click to step simulation
canvas.addEventListener('click', () => {
  game.update();
  game.draw();
});

// Simple animation loop (optional)
/*function loop(){
    game.update();
    game.draw();
    requestAnimationFrame(loop);
}
loop();*/
