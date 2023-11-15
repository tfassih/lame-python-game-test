import os
import tmx
import math
import pygame
from pygame import Rect
from pygame.math import Vector2

os.environ['SDL_VIDEO_CENTERED'] = '1'


###############################################################################
#                               Game State                                    #
###############################################################################

class GameItem():
    def __init__(self, state, position, tile):
        self.state = state
        self.status = "alive"
        self.position = position
        self.tile = tile
        self.orientation = 0


class Unit(GameItem):
    def __init__(self, state, position, tile):
        super().__init__(state, position, tile)
        self.weaponTarget = Vector2(0, 0)
        self.lastBulletEpoch = -100


class Bullet(GameItem):
    def __init__(self, state, unit):
        super().__init__(state, unit.position, Vector2(2, 1))
        self.unit = unit
        self.startPosition = unit.position
        self.endPosition = unit.weaponTarget


class GameState():
    def __init__(self):
        self.epoch = 0
        self.worldSize = Vector2(16, 10)
        self.ground = [[Vector2(5, 1)] * 16] * 10
        self.walls = [[None] * 16] * 10
        self.units = [Unit(self, Vector2(8, 9), Vector2(1, 0))]
        self.bullets = []
        self.bulletSpeed = 0.1
        self.bulletRange = 4
        self.bulletDelay = 5
        self.observers = []

    @property
    def worldWidth(self):
        """
        Returns the world width as an integer
        """
        return int(self.worldSize.x)

    @property
    def worldHeight(self):
        """
        Returns the world height as an integer
        """
        return int(self.worldSize.y)

    def isInside(self, position):
        """
        Returns true is position is inside the world
        """
        return position.x >= 0 and position.x < self.worldWidth \
            and position.y >= 0 and position.y < self.worldHeight

    def findUnit(self, position):
        """
        Returns the index of the first unit at position, otherwise None.
        """
        for unit in self.units:
            if int(unit.position.x) == int(position.x) \
                    and int(unit.position.y) == int(position.y):
                return unit
        return None

    def findLiveUnit(self, position):
        """
        Returns the index of the first live unit at position, otherwise None.
        """
        unit = self.findUnit(position)
        if unit is None or unit.status != "alive":
            return None
        return unit

    def addObserver(self, observer):
        """
        Add a game state observer.
        All observer is notified when something happens (see GameStateObserver class)
        """
        self.observers.append(observer)

    def notifyUnitDestroyed(self, unit):
        for observer in self.observers:
            observer.unitDestroyed(unit)


class GameStateObserver():
    def unitDestroyed(self, unit):
        pass


###############################################################################
#                                Commands                                     #
###############################################################################

class Command():
    def run(self):
        raise NotImplementedError()


class MoveCommand(Command):
    """
    This command moves a unit in a given direction
    """

    def __init__(self, state, unit, moveVector):
        self.state = state
        self.unit = unit
        self.moveVector = moveVector

    def run(self):
        unit = self.unit

        # Destroyed units can't move
        if unit.status != "alive":
            return

        # Update unit orientation
        if self.moveVector.x < 0:
            unit.orientation = 90
        elif self.moveVector.x > 0:
            unit.orientation = -90
        if self.moveVector.y < 0:
            unit.orientation = 0
        elif self.moveVector.y > 0:
            unit.orientation = 180

        # Compute new tank position
        newPos = unit.position + self.moveVector

        # Don't allow positions outside the world
        if not self.state.isInside(newPos):
            return

        # Don't allow wall positions
        if not self.state.walls[int(newPos.y)][int(newPos.x)] is None:
            return

        # Don't allow other unit positions
        unitIndex = self.state.findUnit(newPos)
        if not unitIndex is None:
            return

        unit.position = newPos


class TargetCommand(Command):
    def __init__(self, state, unit, target):
        self.state = state
        self.unit = unit
        self.target = target

    def run(self):
        self.unit.weaponTarget = self.target


class ShootCommand(Command):
    def __init__(self, state, unit):
        self.state = state
        self.unit = unit

    def run(self):
        if self.unit.status != "alive":
            return
        if self.state.epoch - self.unit.lastBulletEpoch < self.state.bulletDelay:
            return
        self.unit.lastBulletEpoch = self.state.epoch
        self.state.bullets.append(Bullet(self.state, self.unit))


class MoveBulletCommand(Command):
    def __init__(self, state, bullet):
        self.state = state
        self.bullet = bullet

    def run(self):
        direction = (self.bullet.endPosition - self.bullet.startPosition).normalize()
        newPos = self.bullet.position + self.state.bulletSpeed * direction
        newCenterPos = newPos + Vector2(0.5, 0.5)
        # If the bullet goes outside the world, destroy it
        if not self.state.isInside(newPos):
            self.bullet.status = "destroyed"
            return
        # If the bullet goes towards the target cell, destroy it
        if ((direction.x >= 0 and newPos.x >= self.bullet.endPosition.x) \
            or (direction.x < 0 and newPos.x <= self.bullet.endPosition.x)) \
                and ((direction.y >= 0 and newPos.y >= self.bullet.endPosition.y) \
                     or (direction.y < 0 and newPos.y <= self.bullet.endPosition.y)):
            self.bullet.status = "destroyed"
            return
        # If the bullet is outside the allowed range, destroy it
        if newPos.distance_to(self.bullet.startPosition) >= self.state.bulletRange:
            self.bullet.status = "destroyed"
            return
        # If the bullet hits a unit, destroy the bullet and the unit
        unit = self.state.findLiveUnit(newCenterPos)
        if not unit is None and unit != self.bullet.unit:
            self.bullet.status = "destroyed"
            unit.status = "destroyed"
            self.state.notifyUnitDestroyed(unit)
            return
        # Nothing happends, continue bullet trajectory
        self.bullet.position = newPos


class DeleteDestroyedCommand(Command):
    def __init__(self, itemList):
        self.itemList = itemList

    def run(self):
        newList = [item for item in self.itemList if item.status == "alive"]
        self.itemList[:] = newList


class LoadLevelCommand(Command):
    def __init__(self, gameMode, fileName):
        self.gameMode = gameMode
        self.fileName = fileName

    def decodeLayer(self, tileMap, layer):
        """
        Decode layer and check layer properties

        Returns the corresponding tileset
        """
        if not isinstance(layer, tmx.Layer):
            raise RuntimeError("Error in {}: invalid layer type".format(self.fileName))
        if len(layer.tiles) != tileMap.width * tileMap.height:
            raise RuntimeError("Error in {}: invalid tiles count".format(self.fileName))

        # Guess which tileset is used by this layer
        gid = None
        for tile in layer.tiles:
            if tile.gid != 0:
                gid = tile.gid
                break
        if gid is None:
            if len(tileMap.tilesets) == 0:
                raise RuntimeError("Error in {}: no tilesets".format(self.fileName))
            tileset = tileMap.tilesets[0]
        else:
            tileset = None
            for t in tileMap.tilesets:
                if gid >= t.firstgid and gid < t.firstgid + t.tilecount:
                    tileset = t
                    break
            if tileset is None:
                raise RuntimeError("Error in {}: no corresponding tileset".format(self.fileName))

        # Check the tileset
        if tileset.columns <= 0:
            raise RuntimeError("Error in {}: invalid columns count".format(self.fileName))
        if tileset.image.data is not None:
            raise RuntimeError("Error in {}: embedded tileset image is not supported".format(self.fileName))

        return tileset

    def decodeArrayLayer(self, tileMap, layer):
        """
        Create an array from a tileMap layer
        """
        tileset = self.decodeLayer(tileMap, layer)

        array = [None] * tileMap.height
        for y in range(tileMap.height):
            array[y] = [None] * tileMap.width
            for x in range(tileMap.width):
                tile = layer.tiles[x + y * tileMap.width]
                if tile.gid == 0:
                    continue
                lid = tile.gid - tileset.firstgid
                if lid < 0 or lid >= tileset.tilecount:
                    raise RuntimeError("Error in {}: invalid tile id".format(self.fileName))
                tileX = lid % tileset.columns
                tileY = lid // tileset.columns
                array[y][x] = Vector2(tileX, tileY)

        return tileset, array

    def decodeUnitsLayer(self, state, tileMap, layer):
        """
        Create a list from a tileMap layer
        """
        tileset = self.decodeLayer(tileMap, layer)

        units = []
        for y in range(tileMap.height):
            for x in range(tileMap.width):
                tile = layer.tiles[x + y * tileMap.width]
                if tile.gid == 0:
                    continue
                lid = tile.gid - tileset.firstgid
                if lid < 0 or lid >= tileset.tilecount:
                    raise RuntimeError("Error in {}: invalid tile id".format(self.fileName))
                tileX = lid % tileset.columns
                tileY = lid // tileset.columns
                unit = Unit(state, Vector2(x, y), Vector2(tileX, tileY))
                units.append(unit)

        return tileset, units

    def run(self):
        # Load map
        if not os.path.exists(self.fileName):
            raise RuntimeError("No file {}".format(self.fileName))
        tileMap = tmx.TileMap.load(self.fileName)

        # Check main properties
        if tileMap.orientation != "orthogonal":
            raise RuntimeError("Error in {}: invalid orientation".format(self.fileName))

        if len(tileMap.layers) != 5:
            raise RuntimeError("Error in {}: 5 layers are expected".format(self.fileName))

        # World size
        state = self.gameMode.gameState
        state.worldSize = Vector2(tileMap.width, tileMap.height)

        # Ground layer
        tileset, array = self.decodeArrayLayer(tileMap, tileMap.layers[0])
        cellSize = Vector2(tileset.tilewidth, tileset.tileheight)
        state.ground[:] = array
        imageFile = tileset.image.source
        self.gameMode.layers[0].setTileset(cellSize, imageFile)

        # Walls layer
        tileset, array = self.decodeArrayLayer(tileMap, tileMap.layers[1])
        if tileset.tilewidth != cellSize.x or tileset.tileheight != cellSize.y:
            raise RuntimeError("Error in {}: tile sizes must be the same in all layers".format(self.fileName))
        state.walls[:] = array
        imageFile = tileset.image.source
        self.gameMode.layers[1].setTileset(cellSize, imageFile)

        # Units layer
        tanksTileset, tanks = self.decodeUnitsLayer(state, tileMap, tileMap.layers[2])
        towersTileset, towers = self.decodeUnitsLayer(state, tileMap, tileMap.layers[3])
        if tanksTileset != towersTileset:
            raise RuntimeError("Error in {}: tanks and towers tilesets must be the same".format(self.fileName))
        if tanksTileset.tilewidth != cellSize.x or tanksTileset.tileheight != cellSize.y:
            raise RuntimeError("Error in {}: tile sizes must be the same in all layers".format(self.fileName))
        state.units[:] = tanks + towers
        cellSize = Vector2(tanksTileset.tilewidth, tanksTileset.tileheight)
        imageFile = tanksTileset.image.source
        self.gameMode.layers[2].setTileset(cellSize, imageFile)

        # Player units
        self.gameMode.playerUnit = tanks[0]

        # Explosions layers
        tileset, array = self.decodeArrayLayer(tileMap, tileMap.layers[4])
        if tileset.tilewidth != cellSize.x or tileset.tileheight != cellSize.y:
            raise RuntimeError("Error in {}: tile sizes must be the same in all layers".format(self.fileName))
        state.bullets.clear()
        imageFile = tileset.image.source
        self.gameMode.layers[3].setTileset(cellSize, imageFile)

        # Window
        windowSize = state.worldSize.elementwise() * cellSize
        self.gameMode.ui.window = pygame.display.set_mode((int(windowSize.x), int(windowSize.y)))

        # Resume game
        self.gameMode.gameOver = False


###############################################################################
#                                Rendering                                    #
###############################################################################

class Layer(GameStateObserver):
    def __init__(self, cellSize, imageFile):
        self.cellSize = cellSize
        self.texture = pygame.image.load(imageFile)

    def setTileset(self, cellSize, imageFile):
        self.cellSize = cellSize
        self.texture = pygame.image.load(imageFile)

    @property
    def cellWidth(self):
        return int(self.cellSize.x)

    @property
    def cellHeight(self):
        return int(self.cellSize.y)

    def unitDestroyed(self, unit):
        pass

    def renderTile(self, surface, position, tile, angle=None):
        # Location on screen
        spritePoint = position.elementwise() * self.cellSize

        # Texture
        texturePoint = tile.elementwise() * self.cellSize
        textureRect = Rect(int(texturePoint.x), int(texturePoint.y), self.cellWidth, self.cellHeight)

        # Draw
        if angle is None:
            surface.blit(self.texture, spritePoint, textureRect)
        else:
            # Extract the tile in a surface
            textureTile = pygame.Surface((self.cellWidth, self.cellHeight), pygame.SRCALPHA)
            textureTile.blit(self.texture, (0, 0), textureRect)
            # Rotate the surface with the tile
            rotatedTile = pygame.transform.rotate(textureTile, angle)
            # Compute the new coordinate on the screen, knowing that we rotate around the center of the tile
            spritePoint.x -= (rotatedTile.get_width() - textureTile.get_width()) // 2
            spritePoint.y -= (rotatedTile.get_height() - textureTile.get_height()) // 2
            # Render the rotatedTile
            surface.blit(rotatedTile, spritePoint)

    def render(self, surface):
        raise NotImplementedError()


class ArrayLayer(Layer):
    def __init__(self, ui, imageFile, gameState, array, surfaceFlags=pygame.SRCALPHA):
        super().__init__(ui, imageFile)
        self.gameState = gameState
        self.array = array
        self.surface = None
        self.surfaceFlags = surfaceFlags

    def setTileset(self, cellSize, imageFile):
        super().setTileset(cellSize, imageFile)
        self.surface = None

    def render(self, surface):
        if self.surface is None:
            self.surface = pygame.Surface(surface.get_size(), flags=self.surfaceFlags)
            for y in range(self.gameState.worldHeight):
                for x in range(self.gameState.worldWidth):
                    tile = self.array[y][x]
                    if not tile is None:
                        self.renderTile(self.surface, Vector2(x, y), tile)
        surface.blit(self.surface, (0, 0))


class UnitsLayer(Layer):
    def __init__(self, ui, imageFile, gameState, units):
        super().__init__(ui, imageFile)
        self.gameState = gameState
        self.units = units

    def render(self, surface):
        for unit in self.units:
            self.renderTile(surface, unit.position, unit.tile, unit.orientation)
            if unit.status == "alive":
                size = unit.weaponTarget - unit.position
                angle = math.atan2(-size.x, -size.y) * 180 / math.pi
                self.renderTile(surface, unit.position, Vector2(0, 6), angle)


class BulletsLayer(Layer):
    def __init__(self, ui, imageFile, gameState, bullets):
        super().__init__(ui, imageFile)
        self.gameState = gameState
        self.bullets = bullets

    def render(self, surface):
        for bullet in self.bullets:
            if bullet.status == "alive":
                self.renderTile(surface, bullet.position, bullet.tile, bullet.orientation)


class ExplosionsLayer(Layer):
    def __init__(self, ui, imageFile):
        super().__init__(ui, imageFile)
        self.explosions = []
        self.maxFrameIndex = 27

    def add(self, position):
        self.explosions.append({
            'position': position,
            'frameIndex': 0
        })

    def unitDestroyed(self, unit):
        self.add(unit.position)

    def render(self, surface):
        for explosion in self.explosions:
            frameIndex = math.floor(explosion['frameIndex'])
            self.renderTile(surface, explosion['position'], Vector2(frameIndex, 4))
            explosion['frameIndex'] += 0.5
        self.explosions = [explosion for explosion in self.explosions if explosion['frameIndex'] < self.maxFrameIndex]


###############################################################################
#                                Game Modes                                   #
###############################################################################

class GameMode():
    def processInput(self):
        raise NotImplementedError()

    def update(self):
        raise NotImplementedError()

    def render(self, window):
        raise NotImplementedError()


class MessageGameMode(GameMode):
    def __init__(self, ui, message):
        self.ui = ui
        self.font = pygame.font.Font("BD_Cartoon_Shout.ttf", 36)
        self.message = message

    def processInput(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.ui.quitGame()
                break
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE \
                        or event.key == pygame.K_SPACE \
                        or event.key == pygame.K_RETURN:
                    self.ui.showMenu()

    def update(self):
        pass

    def render(self, window):
        surface = self.font.render(self.message, True, (200, 0, 0))
        x = (window.get_width() - surface.get_width()) // 2
        y = (window.get_height() - surface.get_height()) // 2
        window.blit(surface, (x, y))


class MenuGameMode(GameMode):
    def __init__(self, ui):
        self.ui = ui

        # Font
        self.titleFont = pygame.font.Font("BD_Cartoon_Shout.ttf", 72)
        self.itemFont = pygame.font.Font("BD_Cartoon_Shout.ttf", 48)

        # Menu items
        self.menuItems = [
            {
                'title': 'Level 1',
                'action': lambda: self.ui.loadLevel("level1.tmx")
            },
            {
                'title': 'Level 2',
                'action': lambda: self.ui.loadLevel("level2.tmx")
            },
            {
                'title': 'Level 3',
                'action': lambda: self.ui.loadLevel("level3.tmx")
            },
            {
                'title': 'Quit',
                'action': lambda: self.ui.quitGame()
            }
        ]

        # Compute menu width
        self.menuWidth = 0
        for item in self.menuItems:
            surface = self.itemFont.render(item['title'], True, (200, 0, 0))
            self.menuWidth = max(self.menuWidth, surface.get_width())
            item['surface'] = surface

        self.currentMenuItem = 0
        self.menuCursor = pygame.image.load("cursor.png")

    def processInput(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.ui.quitGame()
                break
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    self.ui.showGame()
                elif event.key == pygame.K_DOWN:
                    if self.currentMenuItem < len(self.menuItems) - 1:
                        self.currentMenuItem += 1
                elif event.key == pygame.K_UP:
                    if self.currentMenuItem > 0:
                        self.currentMenuItem -= 1
                elif event.key == pygame.K_RETURN:
                    menuItem = self.menuItems[self.currentMenuItem]
                    try:
                        menuItem['action']()
                    except Exception as ex:
                        print(ex)

    def update(self):
        pass

    def render(self, window):
        # Initial y
        y = 50

        # Title
        surface = self.titleFont.render("TANK BATTLEGROUNDS !!", True, (200, 0, 0))
        x = (window.get_width() - surface.get_width()) // 2
        window.blit(surface, (x, y))
        y += (200 * surface.get_height()) // 100

        # Draw menu items
        x = (window.get_width() - self.menuWidth) // 2
        for index, item in enumerate(self.menuItems):
            # Item text
            surface = item['surface']
            window.blit(surface, (x, y))

            # Cursor
            if index == self.currentMenuItem:
                cursorX = x - self.menuCursor.get_width() - 10
                cursorY = y + (surface.get_height() - self.menuCursor.get_height()) // 2
                window.blit(self.menuCursor, (cursorX, cursorY))

            y += (120 * surface.get_height()) // 100


class PlayGameMode(GameMode):
    def __init__(self, ui):
        self.ui = ui

        # Game state
        self.gameState = GameState()

        # Rendering properties
        self.cellSize = Vector2(64, 64)

        # Layers
        self.layers = [
            ArrayLayer(self.cellSize, "ground.png", self.gameState, self.gameState.ground, 0),
            ArrayLayer(self.cellSize, "walls.png", self.gameState, self.gameState.walls),
            UnitsLayer(self.cellSize, "units.png", self.gameState, self.gameState.units),
            BulletsLayer(self.cellSize, "explosions.png", self.gameState, self.gameState.bullets),
            ExplosionsLayer(self.cellSize, "explosions.png")
        ]

        # All layers listen to game state events
        for layer in self.layers:
            self.gameState.addObserver(layer)

        # Controls
        self.playerUnit = self.gameState.units[0]
        self.gameOver = False
        self.commands = []

    @property
    def cellWidth(self):
        return int(self.cellSize.x)

    @property
    def cellHeight(self):
        return int(self.cellSize.y)

    def processInput(self):
        # Pygame events (close, keyboard and mouse click)
        moveVector = Vector2()
        mouseClicked = False
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.ui.quitGame()
                break
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    self.ui.showMenu()
                    break
                elif event.key == pygame.K_RIGHT:
                    moveVector.x = 1
                elif event.key == pygame.K_LEFT:
                    moveVector.x = -1
                elif event.key == pygame.K_DOWN:
                    moveVector.y = 1
                elif event.key == pygame.K_UP:
                    moveVector.y = -1
            elif event.type == pygame.MOUSEBUTTONDOWN:
                mouseClicked = True

        # If the game is over, all commands creations are disabled
        if self.gameOver:
            return

        # Keyboard controls the moves of the player's unit
        if moveVector.x != 0 or moveVector.y != 0:
            self.commands.append(
                MoveCommand(self.gameState, self.playerUnit, moveVector)
            )

        # Mouse controls the target of the player's unit
        mousePos = pygame.mouse.get_pos()
        targetCell = Vector2()
        targetCell.x = mousePos[0] / self.cellWidth - 0.5
        targetCell.y = mousePos[1] / self.cellHeight - 0.5
        command = TargetCommand(self.gameState, self.playerUnit, targetCell)
        self.commands.append(command)

        # Shoot if left mouse was clicked
        if mouseClicked:
            self.commands.append(
                ShootCommand(self.gameState, self.playerUnit)
            )

        # Other units always target the player's unit and shoot if close enough
        for unit in self.gameState.units:
            if unit != self.playerUnit:
                self.commands.append(
                    TargetCommand(self.gameState, unit, self.playerUnit.position)
                )
                if unit.position.distance_to(self.playerUnit.position) <= self.gameState.bulletRange:
                    self.commands.append(
                        ShootCommand(self.gameState, unit)
                    )

        # Bullets automatic movement
        for bullet in self.gameState.bullets:
            self.commands.append(
                MoveBulletCommand(self.gameState, bullet)
            )

        # Delete any destroyed bullet
        self.commands.append(
            DeleteDestroyedCommand(self.gameState.bullets)
        )

    def update(self):
        for command in self.commands:
            command.run()
        self.commands.clear()
        self.gameState.epoch += 1

        # Check game over
        if self.playerUnit.status != "alive":
            self.gameOver = True
            self.ui.showMessage("GAME OVER")
        else:
            oneEnemyStillLives = False
            for unit in self.gameState.units:
                if unit == self.playerUnit:
                    continue
                if unit.status == "alive":
                    oneEnemyStillLives = True
                    break
            if not oneEnemyStillLives:
                self.gameOver = True
                self.ui.showMessage("Victory !")

    def render(self, window):
        for layer in self.layers:
            layer.render(window)


###############################################################################
#                             User Interface                                  #
###############################################################################

class UserInterface():
    def __init__(self):
        # Window
        pygame.init()
        self.window = pygame.display.set_mode((1280, 720))
        pygame.display.set_caption("Discover Python & Patterns - https://www.patternsgameprog.com")
        pygame.display.set_icon(pygame.image.load("icon.png"))

        # Modes
        self.playGameMode = None
        self.overlayGameMode = MenuGameMode(self)
        self.currentActiveMode = 'Overlay'

        # Loop properties
        self.clock = pygame.time.Clock()
        self.running = True

    def loadLevel(self, fileName):
        if self.playGameMode is None:
            self.playGameMode = PlayGameMode(self)
        self.playGameMode.commands.append(LoadLevelCommand(self.playGameMode, fileName))
        try:
            self.playGameMode.update()
            self.currentActiveMode = 'Play'
        except Exception as ex:
            print(ex)
            self.playGameMode = None
            self.showMessage("Level loading failed :-(")

    def showGame(self):
        if self.playGameMode is not None:
            self.currentActiveMode = 'Play'

    def showMenu(self):
        self.overlayGameMode = MenuGameMode(self)
        self.currentActiveMode = 'Overlay'

    def showMessage(self, message):
        self.overlayGameMode = MessageGameMode(self, message)
        self.currentActiveMode = 'Overlay'

    def quitGame(self):
        self.running = False

    def run(self):
        while self.running:
            # Inputs and updates are exclusives
            if self.currentActiveMode == 'Overlay':
                self.overlayGameMode.processInput()
                self.overlayGameMode.update()
            elif self.playGameMode is not None:
                self.playGameMode.processInput()
                try:
                    self.playGameMode.update()
                except Exception as ex:
                    print(ex)
                    self.playGameMode = None
                    self.showMessage("Error during the game update...")

            # Render game (if any), and then the overlay (if active)
            if self.playGameMode is not None:
                self.playGameMode.render(self.window)
            else:
                self.window.fill((0, 0, 0))
            if self.currentActiveMode == 'Overlay':
                darkSurface = pygame.Surface(self.window.get_size(), flags=pygame.SRCALPHA)
                pygame.draw.rect(darkSurface, (0, 0, 0, 150), darkSurface.get_rect())
                self.window.blit(darkSurface, (0, 0))
                self.overlayGameMode.render(self.window)

            # Update display
            pygame.display.update()
            self.clock.tick(60)


userInterface = UserInterface()
userInterface.run()

pygame.quit()
