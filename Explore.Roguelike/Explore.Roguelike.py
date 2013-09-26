import libtcodpy as libtcod
import math
import textwrap
import shelve
from ctypes import *

SCREEN_WIDTH = 80
SCREEN_HEIGHT = 50

MAP_WIDTH = 80
MAP_HEIGHT = 43

LIMIT_FPS = 20
PLAYER_SPEED = 2
DEFAULT_SPEED = 8
DEFAULT_ATTACK_SPEED = 20

ROOM_MAX_SIZE = 10
ROOM_MIN_SIZE = 6
MAX_ROOMS = 30

FOV_ALGO = 0  #default FOV algorithm
FOV_LIGHT_WALLS = True
TORCH_RANGE = 10

INVENTORY_WIDTH = 50
CANCEL_USE = 'cancelled'
HEAL_AMOUNT = 40
LIGHTNING_RANGE = 5
LIGHTNING_DAMAGE = 40
CONFUSE_RANGE = 8
CONFUSE_NUM_TURNS = 10
FIREBALL_RADIUS = 3
FIREBALL_DAMAGE = 25
FIREBALL_RANGE = 10

BAR_WIDTH = 20
PANEL_HEIGHT = 7
PANEL_WIDTH = SCREEN_WIDTH
PANEL_Y = SCREEN_HEIGHT - PANEL_HEIGHT

MSG_X = BAR_WIDTH + 2
MSG_WIDTH = PANEL_WIDTH - MSG_X
MSG_HEIGHT = PANEL_HEIGHT - 1

LEVEL_UP_BASE = 200
LEVEL_UP_FACTOR = 150
LEVEL_SCREEN_WIDTH = 40

CHARACTER_SCREEN_WIDTH = 30

color_dark_wall = libtcod.Color(0, 0, 100)
color_light_wall = libtcod.Color(130, 110, 50)
color_dark_ground = libtcod.Color(50, 50, 150)
color_light_ground = libtcod.Color(200, 180, 50)

class Tile:
    #a tile of the map and its properties
    def __init__(self, blocked, block_sight = None):
        self.blocked = blocked

        #by default, if a tile is blocked, is also blocks sight
        if block_sight is None: block_sight = blocked
        self.block_sight = block_sight

        self.explored = False

class Rect:
    #a rectangle on the map. used to characterize a room.
    def __init__(self, x, y, w, h):
        self.x1 = x
        self.y1 = y
        self.x2 = x + w
        self.y2 = y + h

    def center(self):
        center_x = (self.x1 + self.x2) / 2
        center_y = (self.y1 + self.y2) / 2
        return (round(center_x), round(center_y))

    def intersect(self, other):
        #returns true if this rectangle intersects with another one
        return (self.x1 <= other.x2 and self.x2 >= other.x1 and
                self.y1 <= other.y2 and self.y2 >= other.y1)

class ConfusedMonster:
    def __init__(self, old_ai, num_turns=CONFUSE_NUM_TURNS):
        self.old_ai = old_ai
        self.num_turns = num_turns

    def take_turn(self):
        if self.num_turns > 0: #still confused...
            #move in a random direction
            self.owner.move(libtcod.random_get_int(0, -1, 1), libtcod.random_get_int(0, -1, 1))
            self.num_turns -= 1
        else:
            self.owner.ai = self.old_ai
            message('The ' + self.owner.name + ' is no longer confused!', libtcod.red)

class Fighter:
    #combat related properties and methods
    def __init__(self, hp, defense, power, xp, death_function=None, attack_speed=DEFAULT_ATTACK_SPEED):
        self.base_max_hp = hp
        self.hp = hp
        self.base_defense = defense
        self.base_power = power
        self.xp = xp
        self.death_function = death_function
        self.attack_speed = attack_speed

    @property
    def power(self):
        bonus = sum(equipment.power_bonus for equipment in get_all_equipped(self.owner))
        return self.base_power + bonus

    @property
    def defense(self):
        bonus = sum(equipment.defense_bonus for equipment in get_all_equipped(self.owner))
        return self.base_defense + bonus

    @property
    def max_hp(self):
        bonus = sum(equipment.max_hp_bonus for equipment in get_all_equipped(self.owner))
        return self.base_max_hp + bonus

    def take_damage(self, damage):
        #apply damage if possible
        if damage > 0:
            self.hp -= damage
        if self.hp <= 0:
            function = self.death_function
            if function is not None:
                function(self.owner)
            if self.owner != player:
                player.fighter.xp += self.xp

    def attack(self, target):
        #a simple formula for attack damage
        damage = self.power - target.fighter.defense
        if damage > 0:
            #make target take some damage
            message (self.owner.name.capitalize() + ' attacks ' + target.name + ' for ' + str(damage) + ' hit points.', libtcod.silver)
            target.fighter.take_damage(damage)
        else:
            message (self.owner.name.capitalize() + ' attacks ' + target.name + ' but it has no effect!', libtcod.silver)
        self.owner.wait = self.attack_speed

    def heal(self, amount):
        #heal by the given amount, without going over maximum
        self.hp += amount
        if self.hp > self.max_hp:
            self.hp = self.max_hp

class BasicMonster:
    #AI for a basic monster
    def take_turn(self):
        #a basic monster takes its turn. If you can see it, it can see you
        monster = self.owner
        if libtcod.map_is_in_fov(fov_map, monster.x, monster.y):

            #move towards player if far away
            if monster.distance_to(player) >= 2:
                monster.move_towards(player.x, player.y)

            #close enough, attack!
            elif player.fighter.hp > 0:
                monster.fighter.attack(player)

class Object:
    #this is a generic object: the player, a monster, an item, stairs
    #it's always represented by a character on screen.
    def __init__(self, x, y, char, name, color, blocks=False, always_visible=False, fighter=None, ai=None, speed=DEFAULT_SPEED, item=None, equipment=None):
        self.x = x
        self.y = y
        self.char = char
        self.name = name
        self.color = color
        self.blocks = blocks
        self.always_visible = always_visible
        self.speed = speed
        self.wait = 0
        
        self.fighter = fighter
        if self.fighter:
            self.fighter.owner = self

        self.ai = ai
        if self.ai:
            self.ai.owner = self

        self.item = item
        if self.item:
            self.item.owner = self

        self.equipment = equipment
        if self.equipment:
            self.equipment.owner = self
            self.item = Item()
            self.item.owner = self

    def move(self, dx, dy):
        #move by the given amount, if the destination is not blocked
        if not is_blocked(self.x + dx, self.y + dy):
            self.x += dx
            self.y += dy
        self.wait = self.speed

    def move_towards(self, target_x, target_y):
        #vector from this object to the target, and distance
        dx = target_x - self.x
        dy = target_y - self.y
        distance = math.sqrt(dx ** 2 + dy ** 2)

        #normalize it to length 1, preserving direction, then round it and
        #  convert to integer so the movement is restricted to the map grid
        dx = int(round(dx / distance))
        dy = int(round(dy / distance))
        self.move(dx, dy)

    def distance_to(self, other):
        #return the distance to another object
        dx = other.x - self.x
        dy = other.y - self.y
        return math.sqrt(dx ** 2 + dy ** 2)

    def distance(self, x, y):
        #return the distance to some coordinates
        return math.sqrt((x - self.x) ** 2 + (y - self.y) ** 2)

    def draw(self):
        #set the color and then draw the character that represents this object
        # at its position, if it's in fov or is known
        if (libtcod.map_is_in_fov(fov_map, self.x, self.y) or
            (self.always_visible and map[self.x][self.y].explored)):
            libtcod.console_set_default_foreground(con_map, self.color)
            libtcod.console_put_char(con_map, self.x, self.y, self.char, libtcod.BKGND_NONE)

    def clear(self):
        #erase the character that represents this object
        libtcod.console_put_char(con_map, self.x, self.y, ' ', libtcod.BKGND_NONE)

    def send_to_back(self):
        global objects
        objects.remove(self)
        objects.insert(0, self)

class Item:
    #An item that can be picked up and used.
    def __init__(self, use_function=None):
        self.use_function = use_function

    def use(self):
        #special case: if the object has the Equipment component, the "use" action is to equip/dequip
        if self.owner.equipment:
            self.owner.equipment.toggle_equip()
            return
        #just call the "use_function" if it is defined
        if self.use_function is None:
            message('The ' + self.owner.name + ' cannot be used.')
        else:
            if self.use_function() != CANCEL_USE:
                inventory.remove(self.owner) #destroy after use, unless it was cancelled for some reason

    def pick_up(self):
        #add to player's inventory and remove from map
        if len(inventory) >= 26:
            message('Your inventory is full, cannot pick up ' + self.owner.name + '.', libtcod.red)
        else:
            inventory.append(self.owner)
            objects.remove(self.owner)
            message('You picked up a ' + self.owner.name + '!', libtcod.green)
            
            equipment = self.owner.equipment
            if equipment and get_equipped_in_slot(equipment.slot) is None:
                equipment.equip()

    def drop(self):
        if self.owner.equipment:
            self.owner.equipment.dequip()
        #add to the map and remove from the player's inventory.
        objects.append(self.owner)
        inventory.remove(self.owner)
        self.owner.x = player.x
        self.owner.y = player.y
        message('You dropped a ' + self.owner.name + '.', libtcod.yellow)

class Equipment:
    #an object that can be equipped, yielding bonuses. Automatically adds the item component
    def __init__(self, slot, power_bonus=0, defense_bonus=0, max_hp_bonus=0):
        self.slot = slot
        self.power_bonus = power_bonus
        self.defense_bonus = defense_bonus
        self.max_hp_bonus = max_hp_bonus
        self.is_equipped = False

    def toggle_equip(self):
        if self.is_equipped:
            self.dequip()
        else:
            self.equip()

    def equip(self):
        if self.is_equipped: return

        old_equipment = get_equipped_in_slot(self.slot)
        if old_equipment is not None:
            old_equipment.dequip()

        self.is_equipped = True
        message('Equipped ' + self.owner.name + ' on ' + self.slot + '.', libtcod.light_green)

    def dequip(self):
        if not self.is_equipped: return
        self.is_equipped = False
        message('Dequipped ' + self.owner.name + ' from ' + self.slot + '.', libtcod.light_yellow)

def create_room(room):
    global map
    #go through the tiles in the rectangle and make them passable
    for x in range(room.x1 + 1, room.x2):
        for y in range(room.y1 + 1, room.y2):
            map[x][y].blocked = False
            map[x][y].block_sight = False

def create_h_tunnel(x1, x2, y):
    global map
    for x in range(min(x1, x2), max(x1, x2) +1):
        map[x][y].blocked = False
        map[x][y].block_sight = False

def create_v_tunnel(y1, y2, x):
    global map
    for y in range(min(y1, y2), max(y1, y2) +1):
        map[x][y].blocked = False
        map[x][y].block_sight = False

def make_map():
    global map, objects, stairs

    objects = [player]

    #fill map with "blocked" tiles
    map = [[ Tile(True)
        for y in range(MAP_HEIGHT)]
            for x in range(MAP_WIDTH)]

    rooms = []
    num_rooms = 0

    for r in range(MAX_ROOMS):
        #random width and height
        w = libtcod.random_get_int(0, ROOM_MIN_SIZE, ROOM_MAX_SIZE)
        h = libtcod.random_get_int(0, ROOM_MIN_SIZE, ROOM_MAX_SIZE)
        #random position without going out of the boundaries of the map
        x = libtcod.random_get_int(0, 0, MAP_WIDTH - w - 1)
        y = libtcod.random_get_int(0, 0, MAP_HEIGHT - h - 1)

        #"Rect" class makes rectangles easier to work with
        new_room = Rect(x, y, w, h)

        #run through the other rooms and see if they intersect with this one
        failed = False
        for other_room in rooms:
            if new_room.intersect(other_room):
                failed = True
                break

        if not failed:
            #this means there are no intesections, so this room is valid
        
            #"paint" it to the map's tiles
            create_room(new_room)
            place_objects(new_room)

            #center coordinates of the new room, will be useful later
            (new_x, new_y) = new_room.center()

            if num_rooms == 0:
                #this is the first room, where the player starts at.
                player.x = new_x
                player.y = new_y
            else:
                #all the other rooms
                #connect it to the previous room with a tunnel
            
                #center coordinates of previous room
                (prev_x, prev_y) = rooms[num_rooms-1].center()
            
                #flip a coin
                if libtcod.random_get_int(0, 0, 1) == 1:
                   #first move horizontally, then vertically 
                   create_h_tunnel(prev_x, new_x, prev_y)
                   create_v_tunnel(prev_y, new_y, new_x)
                else:
                   #first move vertically, then horizontally 
                   create_v_tunnel(prev_y, new_y, prev_x)
                   create_h_tunnel(prev_x, new_x, new_y)
            
            #finally, append new room to list
            rooms.append(new_room)
            num_rooms += 1
    stairs = Object(new_x, new_y, '<', 'stairs', libtcod.white, always_visible=True)
    objects.append(stairs)
    stairs.send_to_back()

def random_monster():
    monster_chances = {}
    monster_chances['orc'] = 80
    monster_chances['troll'] = from_dungeon_level([[15, 3], [30, 5], [60, 7]])

    choice = random_choice(monster_chances)

    if choice == 'orc': #80% chance of getting an orc
        #create an orc
        fighter_component = Fighter(hp=20, defense=0, power=4, xp=35, death_function=monster_death)
        ai_component = BasicMonster()
        return Object(0, 0, 'o', 'Orc', libtcod.desaturated_green, 
                            blocks = True, fighter = fighter_component, ai = ai_component)
    elif choice == 'troll':
        #create a troll
        fighter_component = Fighter(hp=30, defense=2, power=8, xp=100, death_function=monster_death)
        ai_component = BasicMonster()
        return Object(0, 0, 'T', 'Troll', libtcod.darker_green, blocks = True, 
                            fighter = fighter_component, ai = ai_component)


def random_item():
    item_chances = {}

    item_chances['heal'] = 35
    item_chances['lightning'] = from_dungeon_level([[25, 4]])
    item_chances['fireball'] = from_dungeon_level([[25, 6]])
    item_chances['confuse'] = from_dungeon_level([[10, 2]])
    item_chances['sword'] = from_dungeon_level([[10, 3]])
    item_chances['shield'] = from_dungeon_level([[10, 6]])

    choice = random_choice(item_chances)

    if choice == 'heal':
        item_component = Item(use_function=cast_heal)
        return Object(0, 0, '!', 'healing potion', libtcod.violet, item=item_component)
    elif choice == 'confuse':
        item_component = Item(use_function=cast_confuse)
        return Object(0, 0, '#', 'scroll of confusion', libtcod.light_yellow, item=item_component)
    elif choice == 'fireball':
        item_component = Item(use_function=cast_fireball)
        return Object(0, 0, '#', 'scroll of fireball', libtcod.light_yellow, item=item_component)
    elif choice == 'confuse':
        item_component = Item(use_function=cast_lightning)
        return Object(0, 0, '#', 'scroll of lightning bolt', libtcod.light_yellow, item=item_component)
    elif choice == 'sword':
        equipment_component = Equipment(slot='right hand', power_bonus=3)
        return Object(0, 0, '/', 'sword', libtcod.sky, equipment=equipment_component)
    elif choice == 'shield':
        equipment_component = Equipment(slot='left hand', defense_bonus=1)
        return Object(0, 0, '[', 'shield', libtcod.sky, equipment=equipment_component)

def random_choice_index(chances):#choose one option from a list of choices, returning the index
    #the dice will land on some number between 1 and the sum of the chances
    dice = libtcod.random_get_int(0, 1, sum(chances))

    running_sum = 0
    choice = 0
    for w in chances:
        running_sum += w
        if dice <= running_sum:
            return choice
        choice += 1

def random_choice(chances_dict):
    chances = chances_dict.values()
    strings = list(chances_dict.keys())
    choice = random_choice_index(chances)
    return strings[choice]

def from_dungeon_level(table):
    #returns a value that depends on level. the table specifies what value occurs after each level
    for (value, level) in reversed(table):
        if dungeon_level >= level:
            return value
    return 0

def next_level():
    global dungeon_level
    message('You taking a moment to rest and recover your strength.', libtcod.light_violet)
    player.fighter.heal(int((player.fighter.max_hp - player.fighter.hp)/2))

    message('After a rare moment of peace, you descend deeper into the heart of the dungeon...', libtcod.red)
    dungeon_level += 1
    make_map()
    initialize_fov()

def get_all_equipped(obj):
    if obj == player:
        equipped_list = []
        for item in inventory:
            if item.equipment and item.equipment.is_equipped:
                equipped_list.append(item.equipment)
        return equipped_list
    else:
        return [] #nothing on anything else yet

def get_equipped_in_slot(slot): #returns the equipment in a slot, or None if empty
    for obj in inventory:
        if obj.equipment and obj.equipment.slot == slot and obj.equipment.is_equipped:
            return obj.equipment
    return None

def target_tile(max_range=None):
    #return the position of a tile left-clicked in player's FOV (optionally in a range), or (None, None) if right-clicked
    global key, mouse
    while True:
        #render the screen. this erases the inventory and shows the names of objects under the mouse
        libtcod.console_flush()
        libtcod.sys_check_for_event(libtcod.EVENT_KEY_PRESS|libtcod.EVENT_MOUSE, key, mouse)
        render_all()

        (x, y) = (mouse.cx, mouse.cy)

        if (mouse.lbutton_pressed and libtcod.map_is_in_fov(fov_map, x, y) and
            (max_range is None or player.distance(x, y) <= max_range)):
            return (x, y)

        if mouse.rbutton_pressed or key.vk == libtcod.KEY_ESCAPE:
            return (None, None) #cancel

def closest_monster(max_range):
    #find closest enemy, up to a maximum range, within player's POV
    closest_enemy = None
    closest_dist = max_range+1
    for object in objects:
        if object.fighter and not object == player and libtcod.map_is_in_fov(fov_map, object.x, object.y):
            dist = player.distance_to(object)
            if dist < closest_dist:
                closest_enemy = object
                closest_dist = dist
    return closest_enemy

def check_level_up():
    level_up_xp = LEVEL_UP_BASE + player.level * LEVEL_UP_FACTOR
    
    if player.fighter.xp >= level_up_xp:
        player.level += 1
        player.fighter.xp -= level_up_xp
        message('Your battle skills grow stronger! You reached level ' + str(player.level) + '!', libtcod.yellow)

        choice = None
        while choice == None:
            choice = menu ('Level up! Choose a stat to raise:\n',
                           ['Constitution (+20 HP, from ' + str(player.fighter.max_hp) + ')',
                            'Strength (+1 attack, from ' + str(player.fighter.power) + ')',
                            'Agility (+1 defense, from ' + str(player.fighter.defense) + ')'],
                            LEVEL_SCREEN_WIDTH)
            if choice == 0:
                player.fighter.base_max_hp += 20
                player.fighter.hp += 20
            elif choice == 1:
                player.fighter.base_power += 1
            elif choice == 2:
                player.fighter.base_defense += 1

def player_move_or_attack(dx, dy):
    global fov_recompute
    x = player.x + dx
    y = player.y + dy

    target = None
    for object in objects:
        if object.fighter and object.x == x and object.y == y:
            target = object
            break
    if target is not None:
        player.fighter.attack(target)
    else:
        player.move(dx, dy)
        fov_recompute = True

def cast_heal():
    #heal the player
    if player.fighter.hp == player.fighter.max_hp:
        message('You are already at full health.', libtcod.red)
        return CANCEL_USE
    
    message('Your wounds start to feel better!', libtcod.light_violet)
    player.fighter.heal(HEAL_AMOUNT)

def cast_lightning():
    #find closest enemy (inside a maximum range) and damage it
    monster = closest_monster(LIGHTNING_RANGE)
    if monster is None: #no enemy in range
        message('No enemy is within range to strike.', libtcod.red)
        return CANCEL_USE
    #zap it!
    message('A ligtning bolt strikes the ' + monster.name + ' with a loud thunder for ' + str(LIGHTNING_DAMAGE) + ' hit points!', libtcod.light_blue)
    monster.fighter.take_damage(LIGHTNING_DAMAGE)

def cast_confuse():
    #find closest enemy (inside a maximum range) and damage it
    monster = closest_monster(CONFUSE_RANGE)
    if monster is None: #no enemy in range
        message('No enemy is close enough to confuse.', libtcod.red)
        return CANCEL_USE
    #zap it!
    message('The eyes of the ' + monster.name + ' look vacant as he stumbles around.', libtcod.light_green)
    old_ai = monster.ai
    monster.ai = ConfusedMonster(old_ai)
    monster.ai.owner = monster

def cast_fireball():
    #ask the player for a target tile to throw a fireball at
    message('Left-click a target tile for the fireball, or right-click to cancel.', libtcod.light_cyan)
    (x, y) = target_tile()
    if x is None: return CANCEL_USE
    message('The fireball explodes, burning everything within ' + str(FIREBALL_RADIUS) + ' tiles.', libtcod.orange)

    for obj in objects: #damage every fighter in range, including the player
        if obj.distance(x, y) <= FIREBALL_RADIUS and obj.fighter:
            message('The ' + obj.name + ' gets burned for ' + str(FIREBALL_DAMAGE) + ' hit points.', libtcod.orange)
            obj.fighter.take_damage(FIREBALL_DAMAGE)

def player_death(player):
    global game_state
    message ('You died!', libtcod.red)
    game_state = 'dead'

    player.char = '%'
    player.color = libtcod.dark_red

def monster_death(monster):
    message (monster.name.capitalize() + ' is dead! You gain ' + str(monster.fighter.xp) + ' experience points.', libtcod.orange)
    monster.char = '%'
    monster.color = libtcod.dark_red
    monster.blocks = False
    monster.fighter = None
    monster.ai = None
    monster.name = 'remains of ' + monster.name
    monster.send_to_back()

def place_objects(room):
    #choose ramdom number of monsters
    max_monsters = from_dungeon_level([[2, 1], [3, 4], [5, 6]])
    num_monsters = libtcod.random_get_int(0, 0, max_monsters)    

    for i in range(num_monsters):
        #choose random spot for this monster
        x = libtcod.random_get_int(0, room.x1+1, room.x2-1)
        y = libtcod.random_get_int(0, room.y1+1, room.y2-1)

        if not is_blocked(x, y):
            monster = random_monster()
            if not monster == None:
                monster.x = x
                monster.y = y
                objects.append(monster)

    #place a random number of items
    max_items = from_dungeon_level([[1, 1], [2, 4]])
    num_items = libtcod.random_get_int(0, 0, max_items)

    for i in range(num_items):
        x = libtcod.random_get_int(0, room.x1+1, room.x2-1)
        y = libtcod.random_get_int(0, room.y1+1, room.y2-1)

        if not is_blocked(x, y):
            item = random_item()
            if not item == None:
                item.x = x
                item.y = y
                objects.append(item)
                item.send_to_back()

def is_blocked(x, y):
    #first test the map tile
    if map[x][y].blocked:
        return True

    #now check for any blocking objects
    for object in objects:
        if object.blocks and object.x == x and object.y == y:
            return True

    return False

################
# Status Panel #
################
con_status = libtcod.console_new(PANEL_WIDTH, PANEL_HEIGHT)

def render_bar(x, y, total_width, name, value, maximum, bar_color, back_color):
    #render a bar (HP, experience, etc). First calculate the width of the bar
    bar_width = int(float(value) / maximum * total_width)

    #background
    libtcod.console_set_default_background(con_status, back_color)
    libtcod.console_rect(con_status, x, y, total_width, 1, False, libtcod.BKGND_SCREEN)
    #bar
    libtcod.console_set_default_background(con_status, bar_color)
    if bar_width > 0:
        libtcod.console_rect(con_status, x, y, bar_width, 1, False, libtcod.BKGND_SCREEN)
    #description
    libtcod.console_set_default_foreground(con_status, libtcod.white)
    libtcod.console_print_ex(con_status, int(x + total_width / 2), y, libtcod.BKGND_NONE, libtcod.CENTER,
                             name + ': ' + str(value) + '/' + str(maximum))

###############
# Menu Panels #
###############

def menu(header, options, width):
    if len(options) > 26: raise ValueError('Cannot have a menu with more than 26 options.')
    #calculate the total height for the header (after auto-wrap) and one line per option
    header_height = libtcod.console_get_height_rect(con_map, 0, 0, width, SCREEN_HEIGHT, header)
    if header == '':
        header_height = 0

    height = len(options) + header_height
    
    #create an off-screen console that represents the menu's window
    window = libtcod.console_new(width, height)

    #print the header, with auto-wrap
    libtcod.console_set_default_foreground(window, libtcod.white)
    libtcod.console_print_rect_ex(window, 0, 0, width, height, libtcod.BKGND_NONE, libtcod.LEFT, header)

    #print the options
    y = header_height
    letter_index = ord('a')
    for option_text in options:
        text = '(' + chr(letter_index) + ') ' + option_text
        libtcod.console_print_ex(window, 0, y, libtcod.BKGND_NONE, libtcod.LEFT, text)
        y += 1
        letter_index += 1

    #blit the contents of "window" to the root console
    x = int(SCREEN_WIDTH/2 - width/2)
    y = int(SCREEN_HEIGHT/2 - height/2)
    libtcod.console_blit(window, 0, 0, width, height, 0, x, y, 1.0, 0.7)
    libtcod.console_flush()

    key = libtcod.console_wait_for_keypress(True)
    if key.vk == libtcod.KEY_ENTER and key.lalt:
        #Alt+Enter: toggle fullscreen
        libtcod.console_set_fullscreen(not libtcod.console_is_fullscreen())
    #convert the ASCII code to an index; if it corresponds to an option, return it
    index = key.c - ord('a')
    if index >= 0 and index < len(options): return index
    return None

def inventory_menu(header):
    #show a menu with each item of the inventory as an option
    if len(inventory) == 0:
        options = ['Inventory is empty.']
    else:
        options = []
        for item in inventory:
            text = item.name
            #show additional information if needed
            if item.equipment and item.equipment.is_equipped:
                text = text + ' (on ' + item.equipment.slot + ')'
            options.append(text)

    index = menu(header, options, INVENTORY_WIDTH)
    #if an item was chosen, return it
    if index is None or len(inventory) == 0: return None
    return inventory[index].item

def msgbox(text, width=50):
    menu(text, [], width) #use menu() as a sort of message box

def main_menu():
    img = libtcod.image_load(b'menu_background1.png')
    
    while not libtcod.console_is_window_closed():

        #show the background image, at twice the regular console resolution
        libtcod.image_blit_2x(img, 0, 0, 0)

        #show the game's title and some credits!
        libtcod.console_set_default_foreground(0, libtcod.light_yellow)
        libtcod.console_print_ex(0, int(SCREEN_WIDTH/2), int(SCREEN_HEIGHT/2-6), libtcod.BKGND_NONE, libtcod.CENTER,
                                 'TOMBS OF THE ANCIENT KINGS')
        libtcod.console_print_ex(0, int(SCREEN_WIDTH/2), int(SCREEN_HEIGHT/2-4), libtcod.BKGND_NONE, libtcod.CENTER,
                                 'By Litho (kudos to Jotaf for tut)')
        

        #show options and wait for player's choice
        choice = menu('', ['Play a new game', 'Continue last game', 'Quit'], 24)

        if choice == 0:
            new_game()
            play_game()

        elif choice == 1:
            try:
                load_game()
            except:
                msgbox('\n No saved game to load. \n', 24)
                continue
            play_game()

        elif choice == 2:
            save_game()
            break

#################
# Message Panel #
#################
def message(new_msg, color = libtcod.white):
    #linewrap the message if necessary
    new_msg_lines = textwrap.wrap(new_msg, MSG_WIDTH)

    for line in new_msg_lines:
        #if the buffer is full, remove the first line to make room
        if len(game_msgs) == MSG_HEIGHT:
            del game_msgs[0]

        game_msgs.append((line, color))

##################
# Main Functions #
##################
def get_names_under_mouse():
    global mouse

    (x, y) = (mouse.cx, mouse.cy)

    names = [obj.name for obj in objects 
             if obj.x == x and obj.y == y and libtcod.map_is_in_fov(fov_map, x, y)]
    
    names = ', '.join(names)
    return names.capitalize()

def handle_keys():
    global fov_recompute, mouse, key

    check_level_up()
    if key.vk == libtcod.KEY_ENTER and key.lalt:
        #Alt+Enter: toggle fullscreen
        libtcod.console_set_fullscreen(not libtcod.console_is_fullscreen())

    elif key.vk == libtcod.KEY_ESCAPE:
        return 'exit'

    if game_state == 'playing':
        if player.wait > 0:
            player.wait -= 1
            return

        #movement keys
        if libtcod.console_is_key_pressed(libtcod.KEY_UP) or key.vk == libtcod.KEY_KP8:
            player_move_or_attack(0, -1)
        elif libtcod.console_is_key_pressed(libtcod.KEY_DOWN) or key.vk == libtcod.KEY_KP2:
            player_move_or_attack(0, 1)
        elif libtcod.console_is_key_pressed(libtcod.KEY_LEFT) or key.vk == libtcod.KEY_KP4:
            player_move_or_attack(-1, 0)
        elif libtcod.console_is_key_pressed(libtcod.KEY_RIGHT) or key.vk == libtcod.KEY_KP6:
            player_move_or_attack(1, 0)
        elif libtcod.console_is_key_pressed(libtcod.KEY_HOME) or key.vk == libtcod.KEY_KP7:
            player_move_or_attack(-1, -1)
        elif libtcod.console_is_key_pressed(libtcod.KEY_PAGEUP) or key.vk == libtcod.KEY_KP9:
            player_move_or_attack(1, -1)
        elif libtcod.console_is_key_pressed(libtcod.KEY_END) or key.vk == libtcod.KEY_KP1:
            player_move_or_attack(-1, 1)
        elif libtcod.console_is_key_pressed(libtcod.KEY_PAGEDOWN) or key.vk == libtcod.KEY_KP3:
            player_move_or_attack(1, 1)
        elif libtcod.console_is_key_pressed(libtcod.KEY_KP5):
            pass

        if not fov_recompute:
            key_char = chr(key.c)

            if key_char == 'g':
                #try to pick up an item
                for object in objects:
                    if object.x == player.x and object.y == player.y and object.item:
                        object.item.pick_up()
                        break

            if key_char == 'i':
                #show the inventory
                chosen_item = inventory_menu('Press the key next to an item to use it, or any other to cancel.\n')
                if chosen_item is not None:
                    chosen_item.use()

            if key_char == 'd':
                chosen_item = inventory_menu('Press the key next to an item to drop it, or any other to cancel.\n')
                if chosen_item is not None:
                    chosen_item.drop()

            if key_char == '<':
                if stairs.x == player.x and stairs.y == player.y:
                    next_level()

            if key_char == 'c':
                #show character information
                level_up_xp = LEVEL_UP_BASE + player.level + LEVEL_UP_FACTOR
                msgbox('Character Information\n' +
                       '\nLevel: ' + str(player.level) +
                       '\nExperience: ' + str(player.fighter.xp) +
                       '\nExperience to level up: ' + str(level_up_xp) + 
                       '\n\nMaximum HP: ' + str(player.fighter.max_hp) +
                       '\nAttack: ' + str(player.fighter.power) +
                       '\nDefense: ' + str(player.fighter.defense), CHARACTER_SCREEN_WIDTH)

            return 'didnt-take-turn'

def render_all():
    global color_dark_wall, color_light_wall
    global color_dark_ground, color_light_ground
    global fov_map, fov_recompute

    if fov_recompute:
        #recompute FOV if needed
        fov_recompute = False
        libtcod.map_compute_fov(fov_map, player.x, player.y, TORCH_RANGE, FOV_LIGHT_WALLS, FOV_ALGO)

    #go through all tiles and set their background color
    for y in range(MAP_HEIGHT):
        for x in range(MAP_WIDTH):
            visible = libtcod.map_is_in_fov(fov_map, x, y)
            wall = map[x][y].block_sight
            if visible:
                map[x][y].explored = True
                if wall:
                    libtcod.console_set_char_background(con_map, x, y, color_light_wall, libtcod.BKGND_SET)
                else:
                    libtcod.console_set_char_background(con_map, x, y, color_light_ground, libtcod.BKGND_SET)
            elif map[x][y].explored:
                if wall:
                    libtcod.console_set_char_background(con_map, x, y, color_dark_wall, libtcod.BKGND_SET)
                else:
                    libtcod.console_set_char_background(con_map, x, y, color_dark_ground, libtcod.BKGND_SET)
            else:
                libtcod.console_set_char_background(con_map, x, y, libtcod.Color(0, 0, 0), libtcod.BKGND_SET)

    for object in objects:
        if object != player:
            object.draw()
    player.draw()

    #blit the contents of "con_map" to the root console
    libtcod.console_blit(con_map, 0, 0, MAP_WIDTH, MAP_HEIGHT, 0, 0, 0)
    
    #show the player's stats
    libtcod.console_set_default_background(con_status, libtcod.black)
    libtcod.console_clear(con_status)

    render_bar(1, 1, BAR_WIDTH, 'HP', player.fighter.hp, player.fighter.max_hp,
               libtcod.light_red, libtcod.darker_red)
    libtcod.console_print_ex(con_status, 1, 3, libtcod.BKGND_NONE, libtcod.LEFT, 'Dungeon level ' + str(dungeon_level))
    libtcod.console_set_default_foreground(con_status, libtcod.light_gray)
    libtcod.console_print_ex(con_status, 1, 0, libtcod.BKGND_NONE, libtcod.LEFT, get_names_under_mouse())

    #print the messages
    y = 1
    for (line, color) in game_msgs:
        libtcod.console_set_default_foreground(con_status, color)
        libtcod.console_print_ex(con_status, MSG_X, y, libtcod.BKGND_NONE, libtcod.LEFT, line)
        y += 1

    libtcod.console_blit(con_status, 0, 0, PANEL_WIDTH, PANEL_HEIGHT, 0, 0, PANEL_Y)

def new_game():
    global player, inventory, game_msgs, game_state, dungeon_level
    
    #create player object
    fighter_component = Fighter(hp=100, defense=1, power=2, xp=0, death_function=player_death)
    player = Object(0, 0, '@', 'player', libtcod.white, blocks=True, fighter=fighter_component, speed=PLAYER_SPEED)
    player.level = 1

    dungeon_level = 1
    make_map()
    initialize_fov()
    game_state = 'playing'

    game_msgs = []

    inventory = []
    equipment_component = Equipment(slot='right hand', power_bonus=2)
    obj = Object(0, 0, '-', 'dagger', libtcod.sky, equipment=equipment_component)
    inventory.append(obj)
    equipment_component.equip()
    obj.always_visible = True

    message('Welcome stranger! Prepare to perish in the Tombs of the Ancient Kings.', libtcod.red)

def initialize_fov():
    libtcod.console_clear(con_map)
    global fov_recompute, fov_map
    fov_recompute = True

    fov_map = libtcod.map_new(MAP_WIDTH, MAP_HEIGHT)
    for y in range(MAP_HEIGHT):
        for x in range(MAP_WIDTH):
            libtcod.map_set_properties(fov_map, x, y, not map[x][y].block_sight, not map[x][y].blocked)

def play_game():
    global key, mouse

    player_action = None

    mouse = libtcod.Mouse()
    key = libtcod.Key()


    while not libtcod.console_is_window_closed():

        libtcod.sys_check_for_event(libtcod.EVENT_KEY_PRESS|libtcod.EVENT_MOUSE,key,mouse)

        render_all()

        libtcod.console_flush()

        for object in objects:
            object.clear()

        player_action = handle_keys()
        if player_action == 'exit':
            main_menu()
            break

        if game_state == 'playing': # and player_action != 'didnt-take-turn':
            for object in objects:
                if object.ai:
                    if object.wait > 0:
                        object.wait -= 1
                    else:
                        object.ai.take_turn()

def save_game():
    #open a new empty shelfe to write the game
    if game_state == 'exit':
        file = shelve.open('savegame', 'n')
        file['map'] = map
        file['objects'] = objects
        file['player_index'] = objects.index(player)
        file['stairs_index'] = objects.index(stairs)
        file['inventory'] = inventory
        file['game_msgs'] = game_msgs
        file['game_state'] = game_state
        file['dungeon_level'] = dungeon_level
        file.close()

def load_game():
    #open the previously saved shelve
    global map, objects, player, inventory, game_msgs, game_state, stairs, dungeon_level

    file = shelve.open('savegame', 'r')
    map = file['map']
    objects = file['objects']
    player = objects[file['player_index']]
    stairs = objects[file['stairs_index']]
    inventory = file['inventory']
    game_msgs = file['game_msgs']
    game_state = file['game_state']
    dungeon_level = file['dungeon_level']
    file.close()
    initialize_fov()

################################
# Initialization and Main Loop #
################################
libtcod.console_set_custom_font(b'arial10x10.png', libtcod.FONT_TYPE_GREYSCALE | libtcod.FONT_LAYOUT_TCOD)
libtcod.console_init_root(SCREEN_WIDTH, SCREEN_HEIGHT, b'python/libtcod tutorial', False)
libtcod.sys_set_fps(LIMIT_FPS)
con_map = libtcod.console_new(MAP_WIDTH, MAP_HEIGHT)

game_state = 'opening'
main_menu()
